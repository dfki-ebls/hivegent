import type { SyncOutput } from "@/lib/chat/tool-part";

export const syncWebSearchOutput: SyncOutput = (input, _text, metadata, addChunk) => {
  if (!input) return;
  if (!Array.isArray(metadata)) return;
  const results = metadata as { title: string; href: string; body: string }[];
  if (!results.length) return;
  const query = input.query as string;
  for (const r of results) {
    if (!r.href) continue;
    addChunk({
      filename: r.href,
      content: r.body || r.title,
      tool: "web",
      detail: query || undefined,
      position: { type: "web_result", url: r.href },
    });
  }
};
