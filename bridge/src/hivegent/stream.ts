/** Project hivegent's UI Message Stream onto the events chat platforms need. */

import { parseJsonEventStream, uiMessageChunkSchema, type UIMessageChunk } from "ai";

export type StreamEvent =
  | { kind: "text"; text: string }
  | { kind: "status"; label: string }
  | { kind: "error"; text: string };

const TOOL_STATUS: Record<string, string> = {
  search: "Searching documents…",
  grep: "Searching documents…",
  list_documents: "Browsing documents…",
  read_document: "Reading documents…",
  read_binary_document: "Reading documents…",
  delegate_task: "Researching…",
  web_search: "Searching the web…",
  web_fetch: "Reading web pages…",
};

function statusLabel(toolName: string | undefined): string {
  return (toolName ? TOOL_STATUS[toolName] : undefined) ?? "Working…";
}

function projectChunk(chunk: UIMessageChunk): StreamEvent | null {
  switch (chunk.type) {
    case "text-delta":
      return { kind: "text", text: chunk.delta };

    case "tool-input-start":
      return { kind: "status", label: statusLabel(chunk.toolName) };

    case "error":
      return { kind: "error", text: chunk.errorText };

    default:
      return null;
  }
}

const CUT_OFF = "The answer was cut off, please ask again.";

/**
 * Follow the turn again with a fresh token, `null` once it ended.
 * hivegent ends a stream when the token that opened it expires, and replays a
 * running turn from its first chunk to whoever follows it again.
 */
export type Reopen = () => Promise<Response | null>;

/**
 * The turn's events up to its `finish`, followed again through *reopen* when a
 * stream ends before it, skipping the chunks already read.
 */
export async function* parseHivegentStream(
  response: Response,
  reopen?: Reopen,
): AsyncGenerator<StreamEvent> {
  let read = 0;
  let current: Response | null = response;

  while (current) {
    const body = current.body;

    if (!body) {
      throw new Error("hivegent response has no body");
    }

    const before = read;
    let index = 0;

    for await (const result of parseJsonEventStream({
      stream: body,
      schema: uiMessageChunkSchema,
    })) {
      index += 1;

      if (index <= read) {
        continue;
      }

      read = index;

      if (!result.success) {
        continue;
      }

      const chunk = result.value;

      if (chunk.type === "finish") {
        return;
      }

      const event = projectChunk(chunk);

      if (event) {
        yield event;
      }
    }

    if (!reopen) {
      return;
    }

    // A stream that brought nothing new is not followed again, so a failing backend ends the loop.
    current = read > before ? await reopen() : null;

    if (!current) {
      yield { kind: "error", text: CUT_OFF };
    }
  }
}
