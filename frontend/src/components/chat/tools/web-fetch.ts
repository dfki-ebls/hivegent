import { z } from "zod";
import { parseBatch } from "@/components/chat/tools/batch";
import type { SyncOutput } from "@/lib/chat/tool-part";

/** One page of the backend ``web_fetch`` tool's payload. */
const WebPageSchema = z.object({
  requested_url: z.string(),
  url: z.string(),
  title: z.string(),
  content: z.string(),
  truncated: z.boolean(),
});

export const syncWebFetchOutput: SyncOutput = ({ metadata, markFullDocument, sourceId }) => {
  for (const page of parseBatch(metadata, WebPageSchema).results) {
    if (!page.content) continue;

    // Store under the final URL (after redirects) and, when it differs,
    // under the requested URL too, since citations may reference either.
    markFullDocument(page.url, page.content, "web", sourceId);

    if (page.requested_url !== page.url) {
      markFullDocument(page.requested_url, page.content, "web", sourceId);
    }
  }
};
