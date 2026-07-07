/** Project hivegent's UI Message Stream onto the events chat platforms need. */

import { parseJsonEventStream, uiMessageChunkSchema, type UIMessageChunk } from "ai";

export type StreamEvent =
  | { kind: "text"; text: string }
  | { kind: "status"; label: string }
  | { kind: "error"; text: string };

const TOOL_STATUS: Record<string, string> = {
  search: "Searching documents…",
  semantic_search: "Searching documents…",
  grep: "Searching documents…",
  glob_documents: "Browsing documents…",
  list_documents: "Browsing documents…",
  read_document: "Reading a document…",
  read_binary_document: "Reading a document…",
  explore: "Researching…",
  web_search: "Searching the web…",
  web_fetch: "Reading a web page…",
  create_plan: "Planning…",
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

export async function* parseHivegentStream(response: Response): AsyncGenerator<StreamEvent> {
  const body = response.body;

  if (!body) {
    throw new Error("hivegent response has no body");
  }

  for await (const result of parseJsonEventStream({ stream: body, schema: uiMessageChunkSchema })) {
    if (!result.success) {
      continue;
    }

    if (result.value.type === "finish") {
      return;
    }

    const event = projectChunk(result.value);

    if (event) {
      yield event;
    }
  }
}
