import type { UIMessage } from "@ai-sdk/react";
import type { ToolPart } from "@/components/ai-elements/tool";
import type { FetchedChunk } from "@/lib/types";

export type { ToolPart };

export type SyncOutput = (
  input: Record<string, unknown> | undefined,
  text: string | null,
  metadata: unknown,
  addChunk: (chunk: Omit<FetchedChunk, "id">) => void,
  markFullDocument: (filename: string, content: string, source: string) => void,
) => void;

export interface ToolPartInfo {
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

export function getToolName(part: { type?: string; toolName?: string }): string | null {
  if (!part.type) return null;
  if (part.type === "dynamic-tool" && part.toolName) return part.toolName;
  if (part.type.startsWith("tool-")) return part.type.replace("tool-", "");
  return null;
}

export function parseJson<T>(value: unknown): T | undefined {
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
export function isToolDataPart(part: unknown): part is { type: "data-tool-output"; data: unknown } {
  return (
    part != null &&
    typeof part === "object" &&
    "type" in part &&
    (part as { type: string }).type === "data-tool-output" &&
    "data" in part
  );
}

export function prettyPrint(value: unknown): string {
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

export function getToolPartInfo(parts: UIMessage["parts"], index: number): ToolPartInfo | null {
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
