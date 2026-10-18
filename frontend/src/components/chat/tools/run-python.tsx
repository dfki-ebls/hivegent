import { useQuery } from "@tanstack/react-query";
import { FileCodeIcon, SquareFunctionIcon } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { HighlightedCode } from "@/components/HighlightedCode";
import { TraceStepCollapsible } from "@/components/chat/TraceStep";
import {
  ChangesetSummarySchema,
  ChangesetView,
  changeCount,
} from "@/components/chat/tools/changeset";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolCode, ToolOutputResult } from "@/components/chat/tools/display";
import { ErrorAlert } from "@/components/ErrorAlert";
import { ScrollDialogContent } from "@/components/ScrollDialogContent";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Item, ItemActions, ItemContent, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { useConversationId } from "@/hooks/chat/use-conversation-id";
import { keyPrefix } from "@/i18n";
import { documentQueries } from "@/queries/documents";
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

/** Mirrors `HostCall` in `backend/src/hivegent/tools/monty.py`. */
const HostCallSchema = z.object({
  function: z.string(),
  arguments: z.string(),
  result: z.string().nullable(),
  error: z.string().nullable(),
});
type HostCall = z.infer<typeof HostCallSchema>;

/** Mirrors `PythonResult` in `backend/src/hivegent/tools/python.py`. */
const PythonResultSchema = z.object({
  result: z.string().nullable(),
  stdout: z.string(),
  script_path: z.string().nullable(),
  changeset: ChangesetOutcomeSchema.nullable(),
  calls: z.array(HostCallSchema),
});
type PythonResult = z.infer<typeof PythonResultSchema>;

/** A blank argument is one the model did not use, as the backend reads it. */
function given(value: string | null | undefined): string | undefined {
  return value?.trim() ? value : undefined;
}

function ScriptSourceView({
  path,
  conversationId,
}: {
  path: string;
  conversationId: string | null;
}) {
  const { data: code, error } = useQuery(documentQueries.content(path, conversationId));

  if (error) {
    return <ErrorAlert>{errorMessage(error)}</ErrorAlert>;
  }

  if (code === undefined) return <Spinner className="mx-auto" />;

  return (
    <HighlightedCode
      code={code}
      language="python"
      lineNumbers
      copyable
      className="min-h-0 flex-1"
      contentClassName="scroll-fade-y"
    />
  );
}

/** The script a call ran by path, whose source opens in a dialog. */
function ScriptFile({ path }: { path: string }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const conversationId = useConversationId();

  return (
    <Item variant="outline" size="xs">
      <ItemMedia>
        <FileCodeIcon />
      </ItemMedia>
      <ItemContent>
        <ItemTitle className="font-mono">{path}</ItemTitle>
      </ItemContent>
      <ItemActions>
        <Dialog>
          <DialogTrigger
            render={
              <Button
                variant="outline"
                size="xs"
                disabled={path.startsWith("/tmp/") && !conversationId}
              />
            }
          >
            {t(($) => $.viewSource)}
          </DialogTrigger>
          <ScrollDialogContent className="h-[min(90dvh,56rem)] sm:max-w-4xl">
            <DialogHeader>
              <DialogTitle className="font-mono">{path}</DialogTitle>
              <DialogDescription>{t(($) => $.sourceDescription)}</DialogDescription>
            </DialogHeader>
            <ScriptSourceView path={path} conversationId={conversationId} />
          </ScrollDialogContent>
        </Dialog>
      </ItemActions>
    </Item>
  );
}

function Program({ code, scriptPath }: { code?: string; scriptPath?: string }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  if (code) {
    return <ToolCode code={code} language="python" title={t(($) => $.inlineProgram)} copyable />;
  }

  if (scriptPath) return <ScriptFile path={scriptPath} />;

  return <p>{t(($) => $.noProgram)}</p>;
}

function ChangesetOutcomeView({ changeset }: { changeset: ChangesetOutcome }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  if (changeset.status === "applied") {
    return (
      <ToolCode
        title={t(($) => $.applied, { count: changeset.reports.length })}
        code={changeset.reports.join("\n")}
      />
    );
  }

  return (
    <>
      <p>{t(($) => $.staged, { count: changeCount(changeset.summary) })}</p>
      <ChangesetView summary={changeset.summary} />
    </>
  );
}

/** The functions the program called on the host, a nested line that opens to each call and its result. */
function HostCalls({ calls }: { calls: HostCall[] }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const lines = calls.map(
    (call) =>
      `${call.function}(${call.arguments}) → ${
        call.error === null ? call.result : t(($) => $.callFailed, { error: call.error })
      }`,
  );

  return (
    <TraceStepCollapsible
      icon={<SquareFunctionIcon />}
      title={t(($) => $.hostCalls, { count: calls.length })}
      status={calls.some((call) => call.error !== null) ? "error" : undefined}
    >
      <ToolCode code={lines.join("\n")} />
    </TraceStepCollapsible>
  );
}

function Output({ result }: { result: PythonResult }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <>
      {result.stdout && (
        <ToolCode title={t(($) => $.printed)} code={result.stdout} language="ansi" />
      )}
      {result.result !== null && <ToolCode title={t(($) => $.returned)} code={result.result} />}
      {!result.stdout && result.result === null && <p>{t(($) => $.noOutput)}</p>}
      {result.changeset && <ChangesetOutcomeView changeset={result.changeset} />}
      {result.calls.length > 0 && <HostCalls calls={result.calls} />}
    </>
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
      parameters={<Program code={code} scriptPath={scriptPath} />}
    >
      {result ? <Output result={result} /> : <ToolOutputResult part={part} />}
    </ToolCard>
  );
}
