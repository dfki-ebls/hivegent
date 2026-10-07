import { FileCodeIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockCopyButton,
  CodeBlockFilename,
  CodeBlockHeader,
} from "@/components/ai-elements/code-block";
import {
  ChangesetSummarySchema,
  ChangesetView,
  changeCount,
} from "@/components/chat/tools/changeset";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolOutputResult, ToolPre, ToolResult, ToolSection } from "@/components/ToolDisplay";
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
import { useConversationId } from "@/hooks/chat/use-conversation-id";
import { getDocumentContent } from "@/lib/api";
import { type ToolPart, toolDisplayName, toolInput } from "@/lib/chat/tool-part";
import { basename, errorMessage } from "@/lib/utils";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.runPython);

interface RunPythonInput {
  code?: string | null;
  script_path?: string | null;
}

/** Mirrors `ChangesetOutcome` in `backend/src/hivegent/tools/changeset.py`. */
const ChangesetOutcomeSchema = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("pending"),
    changeset_id: z.string(),
    summary: ChangesetSummarySchema,
  }),
  z.object({ status: z.literal("applied"), reports: z.array(z.string()) }),
]);
type ChangesetOutcome = z.infer<typeof ChangesetOutcomeSchema>;

/** Mirrors `PythonResult` in `backend/src/hivegent/tools/python.py`. */
const PythonResultSchema = z.object({
  result: z.string().nullable(),
  stdout: z.string(),
  truncated: z.boolean(),
  script_path: z.string().nullable(),
  changeset: ChangesetOutcomeSchema.nullable(),
});
type PythonResult = z.infer<typeof PythonResultSchema>;

/** A blank argument is one the model did not use, as the backend reads it. */
function given(value: string | null | undefined): string | undefined {
  return value?.trim() ? value : undefined;
}

type ScriptSource = { code: string } | { error: string };

function ScriptSourceView({ path, conversationId }: { path: string; conversationId: string | null }) {
  const [source, setSource] = useState<ScriptSource | null>(null);

  useEffect(() => {
    let cancelled = false;

    getDocumentContent(path, conversationId).then(
      (code) => !cancelled && setSource({ code }),
      (cause: unknown) => !cancelled && setSource({ error: errorMessage(cause) }),
    );

    return () => {
      cancelled = true;
    };
  }, [path, conversationId]);

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
  const conversationId = useConversationId();

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="xs" disabled={path.startsWith("/tmp/") && !conversationId}>
          <FileCodeIcon aria-hidden />
          {t(($) => $.viewSource)}
        </Button>
      </DialogTrigger>
      <DialogContent className="flex h-[min(85vh,56rem)] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="font-mono">{path}</DialogTitle>
          <DialogDescription>{t(($) => $.sourceDescription)}</DialogDescription>
        </DialogHeader>
        <ScriptSourceView path={path} conversationId={conversationId} />
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

function Changes({ changeset }: { changeset: ChangesetOutcome }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  if (changeset.status === "applied") {
    return (
      <div className="space-y-2">
        <p>{t(($) => $.applied, { count: changeset.reports.length })}</p>
        <ToolPre boxed>{changeset.reports.join("\n")}</ToolPre>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p>{t(($) => $.staged, { count: changeCount(changeset.summary) })}</p>
      <ChangesetView summary={changeset.summary} />
    </div>
  );
}

function Output({ result }: { result: PythonResult }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <ToolResult>
      {result.stdout && <ToolPre boxed>{result.stdout}</ToolPre>}
      {result.truncated && <p className="text-xs text-muted-foreground">{t(($) => $.truncated)}</p>}
      {result.result !== null && (
        <div>
          <span className="text-muted-foreground">{t(($) => $.returned)}</span>
          <ToolPre boxed className="mt-1">{result.result}</ToolPre>
        </div>
      )}
      {!result.stdout && result.result === null && (
        <p className="text-muted-foreground">{t(($) => $.noOutput)}</p>
      )}
      {result.changeset && <Changes changeset={result.changeset} />}
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
  const result = useMemo(() => PythonResultSchema.safeParse(metadata).data, [metadata]);
  const code = given(input?.code);
  const scriptPath = result?.script_path ?? given(input?.script_path);
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
        </ToolSection>
      }
    >
      {result ? <Output result={result} /> : <ToolOutputResult part={part} />}
    </ToolCard>
  );
}
