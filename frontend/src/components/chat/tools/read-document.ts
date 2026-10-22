import { parseBatch } from "@/components/chat/tools/batch";
import { syncLineRange } from "@/components/chat/tools/line-range";
import { DocumentRangeSchema } from "@/lib/types";
import type { SyncOutput } from "@/lib/chat/tool-part";

/** Each read is a DocumentRange under its own path. */
export const syncReadDocumentOutput: SyncOutput = (ctx) => {
  for (const result of parseBatch(ctx.metadata, DocumentRangeSchema).results) {
    syncLineRange(ctx, result.file_path, result, "read");
  }
};
