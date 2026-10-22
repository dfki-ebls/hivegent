import { z } from "zod";
import { parseBatch } from "@/components/chat/tools/batch";
import { syncLineRange } from "@/components/chat/tools/line-range";
import { LineRangeSchema } from "@/lib/types";
import type { SyncOutput } from "@/lib/chat/tool-part";

/** One page of the backend ``web_fetch`` tool's payload, the window of its text the call showed. */
const WebPageSchema = LineRangeSchema.extend({
  requested_url: z.string(),
  url: z.string(),
  title: z.string(),
});

export const syncWebFetchOutput: SyncOutput = (ctx) => {
  for (const page of parseBatch(ctx.metadata, WebPageSchema).results) {
    // Store under the final URL (after redirects) and, when it differs,
    // under the requested URL too, since citations may reference either.
    syncLineRange(ctx, page.url, page, "web");

    if (page.requested_url !== page.url) {
      syncLineRange(ctx, page.requested_url, page, "web");
    }
  }
};
