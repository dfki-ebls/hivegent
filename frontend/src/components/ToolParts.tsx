import type { UIMessage } from "@ai-sdk/react";
import type {
  ChunkPosition,
  DocumentRange,
  FetchedChunk,
  GrepMatch,
  RetrievedChunk,
} from "@/lib/types";
import type { ToolUIPart } from "ai";

import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
} from "@/components/ai-elements/confirmation";
import {
  Plan,
  PlanAction,
  PlanContent,
  PlanDescription,
  PlanFooter,
  PlanHeader,
  PlanTitle,
  PlanTrigger,
} from "@/components/ai-elements/plan";
import { Tool, ToolContent, ToolHeader, type ToolPart } from "@/components/ai-elements/tool";
import { Button } from "@/components/ui/button";
import { ToolError, ToolParameters, ToolResult } from "@/components/ToolDisplay";
import { snakeCaseToTitleCase } from "@/lib/utils";

export type { ToolPart };

interface ToolPartInfo {
  toolName: string;
  state: ToolPart["state"];
  input: Record<string, unknown> | undefined;
  /**
   * Plain-string tool output.  Set only for tools whose canonical
   * payload is a string (e.g. full-document reads, web fetches) — these
   * never emit a ``data-tool-output`` DataUIPart.
   */
  text: string | null;
  /**
   * Structured tool output, streamed as an adjacent ``data-tool-output``
   * DataUIPart.  Set only for tools that emit one; ``null`` otherwise
   * (either the tool is plain-string or the data part hasn't streamed
   * yet).
   */
  metadata: unknown;
  /** LLM-facing text form (for display in the chat sidebar only). */
  formatted: string | null;
}

interface ToolPartDisplayProps {
  toolName: string;
  part: ToolPart;
  /** Compact text from the ToolOutput envelope. */
  formatted?: string | null;
  onApprove?: (id: string) => void;
  onDeny?: (id: string) => void;
  onExecutePlan?: () => void;
}

function getToolName(part: { type?: string; toolName?: string }): string | null {
  if (!part.type) return null;
  if (part.type === "dynamic-tool" && part.toolName) return part.toolName;
  if (part.type.startsWith("tool-")) return part.type.replace("tool-", "");
  return null;
}

function parseJson<T>(value: unknown): T | undefined {
  if (typeof value === "string") {
    try {
      return JSON.parse(value) as T;
    } catch {
      return undefined;
    }
  }
  return value as T;
}

/** Check whether a message part is a ``data-tool-output`` DataUIPart. */
function isToolDataPart(part: unknown): part is { type: "data-tool-output"; data: unknown } {
  return (
    part != null &&
    typeof part === "object" &&
    "type" in part &&
    (part as { type: string }).type === "data-tool-output" &&
    "data" in part
  );
}

function prettyPrint(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value as number | boolean);
}

/**
 * Update the document store from a tool call's output.
 *
 * Each tool declares a strict payload mode:
 *  - **structured**: consumes only the ``data-tool-output`` DataUIPart
 *    (``metadata``).  The handler skips until that part has arrived;
 *    the LLM-facing text is never parsed.
 *  - **plain-string**: consumes only ``text`` (the ``return_value`` the
 *    LLM sees).  These tools never emit a DataUIPart.
 *
 * ``read_document`` is mixed: its ``start_line`` argument puts it in
 *  structured mode (returning a ``DocumentRange``); otherwise it is
 *  plain-string mode (full document content).
 */
export function processToolOutput(
  toolName: string,
  input: Record<string, unknown> | undefined,
  text: string | null,
  metadata: unknown,
  addChunk: (chunk: Omit<FetchedChunk, "id">) => void,
  markFullDocument: (filename: string, content: string, source: string) => void,
) {
  if (!input) return;

  switch (toolName) {
    case "search": {
      if (!Array.isArray(metadata)) return;
      const chunks = metadata as RetrievedChunk[];
      if (!chunks.length) return;

      const query = input.query as string;
      const source = `search${query ? `: ${query}` : ""}`;
      for (const chunk of chunks) {
        const position: ChunkPosition = {
          type: "line_range",
          startLine: chunk.start_line,
          endLine: chunk.end_line,
        };
        addChunk({
          filename: chunk.filename,
          content: chunk.text,
          source,
          score: chunk.score,
          position,
          startIndex: chunk.start_index,
          endIndex: chunk.end_index,
        });
      }
      return;
    }
    case "read_document": {
      const filename = input.filename as string;
      if (!filename) return;
      const isLineRange = input.start_line != null;

      if (isLineRange) {
        if (metadata == null || typeof metadata !== "object") return;
        if (!("start_line" in (metadata as object))) return;
        const result = metadata as DocumentRange;
        if (!result.content) return;
        const position: ChunkPosition = {
          type: "line_range",
          startLine: result.start_line,
          endLine: result.end_line,
        };
        addChunk({
          filename,
          content: result.content,
          source: `lines ${result.start_line}-${result.end_line}`,
          position,
        });
        return;
      }

      if (typeof text !== "string" || !text) return;
      markFullDocument(filename, text, "read_document");
      return;
    }
    case "grep": {
      if (!Array.isArray(metadata)) return;
      const matches = metadata as GrepMatch[];
      const pattern = input.pattern as string;
      if (!matches.length || !pattern) return;

      const source = `grep: ${pattern}`;
      for (const match of matches) {
        if (match.lines.length === 0) continue;
        const startLine = match.lines[0].line_number;
        const endLine = match.lines[match.lines.length - 1].line_number;
        if (startLine <= 0) continue;

        const position: ChunkPosition =
          match.lines.length > 1
            ? { type: "line_range", startLine, endLine }
            : { type: "line", line: startLine };
        addChunk({
          filename: match.filename,
          content: match.lines.map((l) => l.text).join("\n"),
          source,
          position,
        });
      }
      return;
    }
    case "web_search": {
      if (!Array.isArray(metadata)) return;
      const results = metadata as { title: string; href: string; body: string }[];
      if (!results.length) return;
      const query = input.query as string;
      const source = `web: ${query ?? "search"}`;
      for (const r of results) {
        if (!r.href) continue;
        addChunk({
          filename: r.href,
          content: r.body || r.title,
          source,
          position: { type: "web_result", url: r.href },
        });
      }
      return;
    }
    case "web_fetch": {
      const url = input.url as string;
      if (!url || typeof text !== "string" || !text) return;
      if (text.startsWith("Error:")) return;
      markFullDocument(url, text, "web_fetch");
      return;
    }
  }
}

export function getToolPartInfo(
  parts: UIMessage["parts"],
  index: number,
): ToolPartInfo | null {
  const part = parts[index];
  const typed = part as {
    type: string;
    toolName?: string;
    state?: ToolPart["state"];
    input?: unknown;
    output?: unknown;
  };
  const toolName = getToolName(typed);
  if (!toolName) return null;

  const raw = parseJson<unknown>(typed.output) ?? typed.output;
  const text = typeof raw === "string" ? raw : null;

  // A tool is either ``plain-string`` (no ``data-tool-output`` ever
  // arrives) or ``structured`` (the adjacent DataUIPart carries the
  // canonical payload).  We never parse ``text`` as a stand-in for
  // structured data.
  const next = parts[index + 1];
  const metadata = isToolDataPart(next) ? next.data : null;

  return {
    toolName,
    state: typed.state ?? "output-available",
    input: parseJson<Record<string, unknown>>(typed.input),
    text,
    metadata,
    formatted: text,
  };
}

function CreatePlanToolDisplay({ part, onExecutePlan }: ToolPartDisplayProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = parseJson<{ title?: string; description?: string; steps?: string[] }>(part.input);

  return (
    <Plan defaultOpen isStreaming={state === "input-streaming"}>
      <PlanHeader>
        <div>
          <PlanTitle>{input?.title ?? "Plan"}</PlanTitle>
          {input?.description && <PlanDescription>{input.description}</PlanDescription>}
        </div>
        <PlanAction>
          <PlanTrigger />
        </PlanAction>
      </PlanHeader>
      <PlanContent>
        <ol className="list-decimal space-y-1 pl-5 text-sm">
          {input?.steps?.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      </PlanContent>
      {state === "output-available" && onExecutePlan && (
        <PlanFooter>
          <Button onClick={onExecutePlan}>Execute Plan</Button>
        </PlanFooter>
      )}
    </Plan>
  );
}

export function ToolPartDisplay({
  toolName,
  part,
  formatted,
  onApprove,
  onDeny,
  onExecutePlan,
}: ToolPartDisplayProps) {
  if (toolName === "create_plan") {
    return <CreatePlanToolDisplay toolName={toolName} part={part} onExecutePlan={onExecutePlan} />;
  }

  const state: ToolPart["state"] = part.state ?? "output-available";
  const approval = "approval" in part ? (part as ToolUIPart).approval : undefined;
  const input = parseJson<Record<string, unknown>>(part.input);

  return (
    <Tool defaultOpen={state === "approval-requested"}>
      <ToolHeader title={snakeCaseToTitleCase(toolName)} type={`tool-${toolName}`} state={state} />
      <ToolContent>
        {input && <ToolParameters params={input} />}
        {approval && (
          <Confirmation approval={approval} state={state}>
            <ConfirmationRequest>
              <span className="text-sm">
                Allow the assistant to run <strong>{snakeCaseToTitleCase(toolName)}</strong>?
              </span>
            </ConfirmationRequest>
            <ConfirmationAccepted>
              <span className="text-sm text-green-700 dark:text-green-400">Approved</span>
            </ConfirmationAccepted>
            <ConfirmationRejected>
              <span className="text-sm text-orange-700 dark:text-orange-400">Denied</span>
            </ConfirmationRejected>
            <ConfirmationActions>
              <ConfirmationAction variant="outline" onClick={() => onDeny?.(approval.id ?? "")}>
                Deny
              </ConfirmationAction>
              <ConfirmationAction onClick={() => onApprove?.(approval.id ?? "")}>
                Approve
              </ConfirmationAction>
            </ConfirmationActions>
          </Confirmation>
        )}
        {part.output !== undefined && (
          <ToolResult>
            <pre className="whitespace-pre-wrap text-xs font-mono">
              {formatted ?? prettyPrint(part.output)}
            </pre>
          </ToolResult>
        )}
        {state === "output-error" && part.errorText && <ToolError message={part.errorText} />}
      </ToolContent>
    </Tool>
  );
}
