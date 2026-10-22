import type { SyncOutputContext } from "@/lib/chat/tool-part";
import type { ChunkOrigin, LineRange } from "@/lib/types";

/** Store *range* under *filename*, where spanning the whole text means a full-document fetch. */
export function syncLineRange(
  { addChunk, markFullDocument, sourceId }: SyncOutputContext,
  filename: string,
  range: LineRange,
  origin: ChunkOrigin,
): void {
  if (!range.content) return;

  if (range.start_line === 1 && range.end_line === range.total_lines) {
    markFullDocument(filename, range.content, origin, sourceId, range.total_lines);
    return;
  }

  addChunk(
    {
      filename,
      content: range.content,
      origin,
      position: { type: "line_range", startLine: range.start_line, endLine: range.end_line },
      sourceId,
    },
    range.total_lines,
  );
}
