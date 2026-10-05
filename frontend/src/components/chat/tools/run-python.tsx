import { FileCodeIcon } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
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
import { keyPrefix } from "@/i18n";
import { getDocumentContent } from "@/lib/api";
import { type ToolPart, toolDisplayName, toolInput } from "@/lib/chat/tool-part";
import { basename, errorMessage } from "@/lib/utils";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.runPython);

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
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="xs">
          <FileCodeIcon aria-hidden />
          {t(($) => $.viewSource)}
        </Button>
      </DialogTrigger>
      <DialogContent className="flex h-[min(85vh,56rem)] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="font-mono">{path}</DialogTitle>
          <DialogDescription>{t(($) => $.sourceDescription)}</DialogDescription>
        </DialogHeader>
        <ScriptSourceView path={path} />
      </DialogContent>
    </Dialog>
  );
}

function Program({ code, scriptPath }: { code?: string; scriptPath?: string }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  if (code) {
    return (
      <CodeBlock code={code} language="python">
        <CodeBlockHeader>
          <CodeBlockFilename>{t(($) => $.inlineProgram)}</CodeBlockFilename>
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

  return <p className="text-muted-foreground">{t(($) => $.noProgram)}</p>;
}

/** A workspace path set inline in prose. */
function PathCode({ children }: { children?: ReactNode }) {
  return <code className="font-mono text-xs">{children}</code>;
}

type PathMessage = "savedTo" | "notSaved" | "destination" | "approvalQuestion";

/** A sentence naming a workspace path, set in code within the translated text. */
function PathText({ message, path }: { message: PathMessage; path: string }) {
  return (
    <Trans
      i18nKey={($) => $.chat.tools.runPython[message]}
      values={{ path }}
      components={{ path: <PathCode /> }}
    />
  );
}

const OUTPUT_BLOCK = "max-h-80 overflow-auto rounded-md bg-muted/40 p-2";

function Output({ result, commitPath }: { result: PythonResult; commitPath?: string }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <ToolResult>
      {result.stdout && <ToolPre className={OUTPUT_BLOCK}>{result.stdout}</ToolPre>}
      {result.truncated && <p className="text-xs text-muted-foreground">{t(($) => $.truncated)}</p>}
      {result.result !== null && (
        <div>
          <span className="text-muted-foreground">{t(($) => $.returned)}</span>
          <ToolPre className={`mt-1 ${OUTPUT_BLOCK}`}>{result.result}</ToolPre>
        </div>
      )}
      {!result.stdout && result.result === null && (
        <p className="text-muted-foreground">{t(($) => $.noOutput)}</p>
      )}
      {result.written_file ? (
        <p>
          <PathText message="savedTo" path={result.written_file} />
        </p>
      ) : (
        commitPath && (
          <p className="text-muted-foreground">
            <PathText message="notSaved" path={commitPath} />
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
  const { t } = useTranslation();
  const input = toolInput<RunPythonInput>(part);
  const result = isPythonResult(metadata) ? metadata : null;
  const code = given(input?.code);
  const scriptPath = result?.script_path ?? given(input?.script_path);
  const commitPath = given(input?.commit_path);
  const tool = toolDisplayName(t, "run_python");

  return (
    <ToolCard
      toolName="run_python"
      part={part}
      title={
        scriptPath
          ? t(($) => $.chat.tools.runPython.titleWithScript, { tool, script: basename(scriptPath) })
          : tool
      }
      parameters={
        <ToolSection title={t(($) => $.chat.tools.sections.program)}>
          <Program code={code} scriptPath={scriptPath} />
          {commitPath && (
            <p className="text-muted-foreground">
              <PathText message="destination" path={commitPath} />
            </p>
          )}
        </ToolSection>
      }
      approvalPrompt={
        commitPath && (
          <div className="space-y-1 text-sm">
            <p>
              <PathText message="approvalQuestion" path={commitPath} />
            </p>
            <p className="text-xs text-muted-foreground">
              {t(($) => $.chat.tools.runPython.approvalHint)}
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
