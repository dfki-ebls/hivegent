import {
  type DynamicToolUIPart,
  getToolName,
  isToolUIPart,
  type ToolUIPart,
  type UIMessage,
} from "ai";
import type { TFunction } from "i18next";
import { z } from "zod";
import { isCatalogKey } from "@/i18n";
import { ASK_USER_PART } from "@/lib/chat/ask-user";
import { chat } from "@/i18n/locales/en/chat";
import { parseJson, snakeCaseToTitleCase } from "@/lib/utils";
import type { AddChunk, AddImage, MarkFullDocument } from "@/stores/fetched-documents-store";

/**
 * Where a message stands: the last one while the chat streams, the last one
 * while the chat is idle and waits for the user, or an earlier one.
 */
export type Live = "streaming" | "waiting" | "stale";

export type ToolPart = ToolUIPart | DynamicToolUIPart;

/** What a tool's view renders from. */
export interface ToolRenderProps {
  part: ToolPart;
  metadata: unknown;
  /** Whether the call asks the user something they can still answer, as `blocksOf` places it. */
  prompt: boolean;
}

/** How a call's trace line reads. */
interface LineState {
  /** Whether the call failed or was stopped, which an agent routinely recovers from. */
  failed?: boolean;
  label?: keyof typeof chat.tools.status;
}

const RUNNING: LineState = { label: "running" };
const STOPPED: LineState = { failed: true, label: "stopped" };

const FAILED: LineState = { failed: true, label: "failed" };
const DENIED: LineState = { label: "denied" };

/** Whether *part* is a call a stop cut off, which the backend closes as interrupted. */
function isInterrupted(part: ToolPart): boolean {
  return part.callProviderMetadata?.pydantic_ai?.outcome === "interrupted";
}

/**
 * How a call's line reads, where a call that never finished was stopped once
 * the chat stops streaming. A later message abandons an open request on a stale
 * message, which the backend closes as a denial or, for a question, a dismissal,
 * so it reads as that before the stored turn arrives too.
 */
export function toolLineState(part: ToolPart, live: Live): LineState {
  const streaming = live === "streaming";

  switch (part.state) {
    case "input-streaming":
    case "input-available":
      if (streaming) return RUNNING;

      return part.type === ASK_USER_PART && live === "stale" ? FAILED : STOPPED;
    case "approval-requested":
      return live === "stale" ? DENIED : { label: "awaitingApproval" };
    case "approval-responded":
      if (!part.approval.approved) return { label: "denied" };

      return streaming ? RUNNING : { label: "approved" };
    case "output-available":
      if (isInterrupted(part)) return STOPPED;

      return part.preliminary && streaming ? RUNNING : {};
    case "output-error":
      return FAILED;
    case "output-denied":
      return DENIED;
  }
}

/** Everything a tool handler may draw on to persist what its call captured. */
export interface SyncOutputContext {
  input: Record<string, unknown> | undefined;
  text: string | null;
  metadata: unknown;
  /** Tool call that produced this output, keeping repeated reads distinct. */
  sourceId?: string;
  addChunk: AddChunk;
  markFullDocument: MarkFullDocument;
  addImage: AddImage;
}

export type SyncOutput = (ctx: SyncOutputContext) => void;

interface ToolPartInfo {
  toolName: string;
  toolCallId?: string;
  state: ToolPart["state"];
  input: Record<string, unknown> | undefined;
  /**
   * Plain-string tool output.  Set only for tools whose canonical
   * payload is a string, these never emit a ``data-tool-output``
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
}

const ToolArgsSchema = z.record(z.string(), z.unknown());

/**
 * Read a tool call's arguments in every state.
 * A call rejected before it ran streams as ``tool-input-error``,
 * which the AI SDK stores under ``rawInput`` and leaves ``input`` empty.
 */
export function toolInput(part: {
  input?: unknown;
  rawInput?: unknown;
}): Record<string, unknown> | undefined {
  return toolInputAs(part, ToolArgsSchema);
}

/** A tool call's arguments when they match *schema*, which they may not while streaming. */
export function toolInputAs<T>(
  part: { input?: unknown; rawInput?: unknown },
  schema: z.ZodType<T>,
): T | undefined {
  return schema.safeParse(parseJson(part.input ?? part.rawInput)).data;
}

/** Check whether a message part is a ``data-tool-output`` DataUIPart. */
function isToolDataPart(
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
 * adjacent to its tool part, resolve the correlation once per message
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
  if (!isToolUIPart(part)) return null;

  const raw = parseJson(part.output) ?? part.output;
  const text = typeof raw === "string" ? raw : null;

  // A tool is either ``plain-string`` (no ``data-tool-output`` ever
  // arrives) or ``structured`` (a DataUIPart carries the canonical
  // payload, correlated by tool-call id).  We never parse ``text`` as a
  // stand-in for structured data.
  const metadata = toolData.get(part.toolCallId) ?? null;

  return {
    toolName: getToolName(part),
    toolCallId: part.toolCallId,
    state: part.state,
    input: toolInput(part),
    text,
    metadata,
  };
}

/** A built-in tool's name, which the display-name catalog lists every one of. */
export type ToolName = keyof typeof chat.tools.names;

/** A tool's name for display, title-cased for tools without a catalog entry such as MCP tools. */
export function toolDisplayName(t: TFunction, toolName: string): string {
  return isCatalogKey(chat.tools.names, toolName)
    ? t(($) => $.chat.tools.names[toolName])
    : snakeCaseToTitleCase(toolName);
}
