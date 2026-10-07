import { z } from "zod";
import { parseBatch } from "@/components/chat/tools/batch";
import type { SyncOutput } from "@/lib/chat/tool-part";

/** One search of the backend ``web_search`` tool's payload. */
const WebSearchResultsSchema = z.object({
  query: z.string(),
  edition: z.string(),
  hits: z.array(z.object({ title: z.string(), href: z.string(), body: z.string() })),
});

export const syncWebSearchOutput: SyncOutput = ({ metadata, addChunk }) => {
  for (const search of parseBatch(metadata, WebSearchResultsSchema).results) {
    for (const hit of search.hits) {
      if (!hit.href) continue;

      addChunk({
        filename: hit.href,
        content: hit.body || hit.title,
        origin: "web",
        detail: search.query || undefined,
        position: { type: "web_result", url: hit.href },
      });
    }
  }
};
