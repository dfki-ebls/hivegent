import { FileCodeIcon } from "lucide-react";
import { useEffect, useState } from "react";
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockCopyButton,
  CodeBlockFilename,
  CodeBlockHeader,
} from "@/components/ai-elements/code-block";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolPre, ToolResult, ToolSection } from "@/components/ToolDisplay";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { getDocumentContent } from "@/lib/api";
import { type ToolPart, toolInput } from "@/lib/chat/tool-part";
import { basename, errorMessage } from "@/lib/utils";

interface RunPythonInput {
  code?: string | null;
  script_path?: string | null;
  commit_path?: string | null;
}

/** Mirrors `PythonResult` in `backend/src/hivegent/tools/python.py`. */
interface PythonResult {
  result: string | null;
  stdout: string;
  truncated: boolean;
  script_path: string | null;
  written_file: string | null;
}

function isPythonResult(value: unknown): value is PythonResult {
  return value != null && typeof value === "object" && "stdout" in value && "result" in value;
}

/** A blank argument is one the model did not use, as the backend reads it. */
function given(value: string | null | undefined): string | undefined {
  return value?.trim() ? value : undefined;
}

type ScriptSource = { code: string } | { error: string };

function ScriptSourceView({ path }: { path: string }) {
  const [source, setSource] = useState<ScriptSource | null>(null);

  useEffect(() => {
    let cancelled = false;

    getDocumentContent(path).then(
      (code) => !cancelled && setSource({ code }),
      (cause: unknown) => !cancelled && setSource({ error: errorMessage(cause) }),
    );

    return () => {
      cancelled = true;
    };
  }, [path]);

  if (!source) return <Spinner className="mx-auto" />;

  if ("error" in source) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {source.error}
      </p>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto rounded-md">
      <CodeBlock code={source.code} language="python" showLineNumbers />
    </div>
  );
}

function ScriptSourceDialog({ path }: { path: string }) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="xs">
          <FileCodeIcon aria-hidden />
          View source
        </Button>
      </DialogTrigger>
      <DialogContent className="flex h-[min(85vh,56rem)] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="font-mono">{path}</DialogTitle>
          <DialogDescription>
            The script as stored now. It is read afresh on every run, so a later edit may differ
            from the version this call ran.
          </DialogDescription>
        </DialogHeader>
        <ScriptSourceView path={path} />
      </DialogContent>
    </Dialog>
  );
}

function Program({ code, scriptPath }: { code?: string; scriptPath?: string }) {
  if (code) {
    return (
      <CodeBlock code={code} language="python">
        <CodeBlockHeader>
          <CodeBlockFilename>Inline program</CodeBlockFilename>
          <CodeBlockActions>
            <CodeBlockCopyButton className="size-6" />
          </CodeBlockActions>
        </CodeBlockHeader>
      </CodeBlock>
    );
  }

  if (scriptPath) {
    return (
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate font-mono text-xs" title={scriptPath}>
          {scriptPath}
        </span>
        <ScriptSourceDialog path={scriptPath} />
      </div>
    );
  }

  return (
    <p className="text-muted-foreground">No Python code or script path was provided.</p>
  );
}

/** A workspace path set inline in prose. */
function PathCode({ children }: { children: string }) {
  return <code className="font-mono text-xs">{children}</code>;
}

const OUTPUT_BLOCK = "max-h-80 overflow-auto rounded-md bg-muted/40 p-2";

function Output({ result, commitPath }: { result: PythonResult; commitPath?: string }) {
  return (
    <ToolResult>
      {result.stdout && <ToolPre className={OUTPUT_BLOCK}>{result.stdout}</ToolPre>}
      {result.truncated && (
        <p className="text-xs text-muted-foreground">Printed output was truncated.</p>
      )}
      {result.result !== null && (
        <div>
          <span className="text-muted-foreground">Returned:</span>
          <ToolPre className={`mt-1 ${OUTPUT_BLOCK}`}>{result.result}</ToolPre>
        </div>
      )}
      {!result.stdout && result.result === null && (
        <p className="text-muted-foreground">The program printed nothing and returned no value.</p>
      )}
      {result.written_file ? (
        <p>
          Saved to <PathCode>{result.written_file}</PathCode>.
        </p>
      ) : (
        commitPath && (
          <p className="text-muted-foreground">
            Nothing was saved to <PathCode>{commitPath}</PathCode>.
          </p>
        )
      )}
    </ToolResult>
  );
}

interface RunPythonToolProps {
  part: ToolPart;
  metadata: unknown;
}

export function RunPythonTool({ part, metadata }: RunPythonToolProps) {
  const input = toolInput<RunPythonInput>(part);
  const result = isPythonResult(metadata) ? metadata : null;
  const code = given(input?.code);
  const scriptPath = result?.script_path ?? given(input?.script_path);
  const commitPath = given(input?.commit_path);

  return (
    <ToolCard
      toolName="run_python"
      part={part}
      title={scriptPath ? `Run Python · ${basename(scriptPath)}` : "Run Python"}
      parameters={
        <ToolSection title="Program">
          <Program code={code} scriptPath={scriptPath} />
          {commitPath && (
            <p className="text-muted-foreground">
              Output destination: <PathCode>{commitPath}</PathCode>.
            </p>
          )}
        </ToolSection>
      }
      approvalPrompt={
        commitPath && (
          <div className="space-y-1 text-sm">
            <p>
              Allow this program to create or replace <PathCode>{commitPath}</PathCode>?
            </p>
            <p className="text-xs text-muted-foreground">
              Python runs in an isolated sandbox. It can read your documents and use the
              assistant&apos;s search tools, but the only document it can change is the one named
              here, so saving it waits for your approval. Review the program first.
            </p>
          </div>
        )
      }
    >
      {result ? (
        <Output result={result} commitPath={commitPath} />
      ) : (
        typeof part.output === "string" && (
          <ToolResult>
            <ToolPre>{part.output}</ToolPre>
          </ToolResult>
        )
      )}
    </ToolCard>
  );
}
