import type { UIMessage } from "@ai-sdk/react";
import type { ToolPart } from "@/components/ai-elements/tool";
import type { ChunkOrigin, FetchedChunk, FetchedImage } from "@/lib/types";

export type { ToolPart };

export type SyncOutput = (
  input: Record<string, unknown> | undefined,
  text: string | null,
  metadata: unknown,
  addChunk: (chunk: Omit<FetchedChunk, "id">, totalLines?: number) => void,
  markFullDocument: (
    filename: string,
    content: string,
    origin: ChunkOrigin,
    sourceId?: string,
  ) => void,
  addImage: (filename: string, image: FetchedImage) => void,
  sourceId?: string,
) => void;

export interface ToolPartInfo {
  toolName: string;
  toolCallId?: string;
  state: ToolPart["state"];
  input: Record<string, unknown> | undefined;
  /**
   * Plain-string tool output.  Set only for tools whose canonical
   * payload is a string — these never emit a ``data-tool-output``
   * DataUIPart.
   */
  text: string | null;
  /**
   * Structured tool output, streamed as a ``data-tool-output``
   * DataUIPart whose ``id`` matches this tool call.  Set only for tools
   * that emit one; ``null`` otherwise (either the tool is plain-string
   * or the data part hasn't streamed yet).
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
export function isToolDataPart(
  part: unknown,
): part is { type: "data-tool-output"; id?: string; data: unknown } {
  return (
    part != null &&
    typeof part === "object" &&
    "type" in part &&
    (part as { type: string }).type === "data-tool-output" &&
    "data" in part
  );
}

/**
 * Index a message's structured tool payloads by tool-call id.
 *
 * The backend stamps each ``data-tool-output`` DataUIPart with the
 * originating tool-call id.  The AI SDK appends these to the end of
 * ``message.parts`` in arrival order, so a tool's payload is not
 * adjacent to its tool part — resolve the correlation once per message
 * instead of rescanning the parts for every tool call.
 */
export function indexToolData(parts: UIMessage["parts"]): Map<string, unknown> {
  const byCallId = new Map<string, unknown>();
  for (const part of parts) {
    if (isToolDataPart(part) && part.id) byCallId.set(part.id, part.data);
  }
  return byCallId;
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

export function getToolPartInfo(
  part: UIMessage["parts"][number],
  toolData: ReadonlyMap<string, unknown>,
): ToolPartInfo | null {
  const typed = part as {
    type: string;
    toolName?: string;
    toolCallId?: string;
    state?: ToolPart["state"];
    input?: unknown;
    output?: unknown;
  };
  const toolName = getToolName(typed);
  if (!toolName) return null;

  const raw = parseJson<unknown>(typed.output) ?? typed.output;
  const text = typeof raw === "string" ? raw : null;

  // A tool is either ``plain-string`` (no ``data-tool-output`` ever
  // arrives) or ``structured`` (a DataUIPart carries the canonical
  // payload, correlated by tool-call id).  We never parse ``text`` as a
  // stand-in for structured data.
  const metadata = typed.toolCallId != null ? (toolData.get(typed.toolCallId) ?? null) : null;

  return {
    toolName,
    toolCallId: typed.toolCallId,
    state: typed.state ?? "output-available",
    input: parseJson<Record<string, unknown>>(typed.input),
    text,
    metadata,
    formatted: text,
  };
}
