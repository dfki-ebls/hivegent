import type { ChunkPosition, RetrievedChunk } from "@/lib/types";
import type { SyncOutput } from "@/lib/chat/tool-part";

export const syncSearchOutput: SyncOutput = (
  input,
  _text,
  metadata,
  addChunk,
  _markFullDocument,
  _addImage,
  sourceId,
) => {
  if (!input) return;
  if (!Array.isArray(metadata)) return;
  const chunks = metadata as RetrievedChunk[];
  if (!chunks.length) return;

  const query = input.query as string;
  for (const chunk of chunks) {
    const position: ChunkPosition = {
      type: "line_range",
      startLine: chunk.start_line,
      endLine: chunk.end_line,
    };
    addChunk(
      {
        filename: chunk.filename,
        content: chunk.text,
        origin: "search",
        detail: query || undefined,
        position,
        startIndex: chunk.start_index,
        endIndex: chunk.end_index,
        sourceId,
      },
      undefined,
    );
  }
};
