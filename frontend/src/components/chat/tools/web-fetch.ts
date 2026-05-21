import type { SyncOutput } from "@/lib/chat/tool-part";

export const syncWebFetchOutput: SyncOutput = (
  input,
  text,
  _metadata,
  _addChunk,
  markFullDocument,
) => {
  if (!input) return;
  const url = input.url as string;
  if (!url || typeof text !== "string" || !text) return;
  if (text.startsWith("Error:")) return;
  markFullDocument(url, text, "web_fetch");
};
