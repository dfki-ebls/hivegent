import { z } from "zod";
import { listArgument, parseBatch } from "@/components/chat/tools/batch";
import { type ChunkPosition, RetrievedChunkSchema } from "@/lib/types";
import type { SyncOutput } from "@/lib/chat/tool-part";

/** Mirrors the `queries` argument of `VectorSearchTool` in `backend/src/hivegent/tools/retrieval.py`. */
const SearchInputSchema = z.object({ queries: listArgument(z.string()) });

/** The queries of a search call, whose rankings were fused into one list. */
function searchQueries(input: Record<string, unknown> | undefined): string {
  return SearchInputSchema.safeParse(input).data?.queries.join("; ") ?? "";
}

export const syncSearchOutput: SyncOutput = ({ input, metadata, addChunk, sourceId }) => {
  const detail = searchQueries(input) || undefined;

  for (const chunk of parseBatch(metadata, RetrievedChunkSchema).results) {
    const position: ChunkPosition = {
      type: "line_range",
      startLine: chunk.start_line,
      endLine: chunk.end_line,
    };
    addChunk({
      filename: chunk.filename,
      content: chunk.text,
      origin: "search",
      detail,
      position,
      startIndex: chunk.start_index,
      endIndex: chunk.end_index,
      sourceId,
    });
  }
};
