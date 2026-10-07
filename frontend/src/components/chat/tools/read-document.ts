import { parseBatch } from "@/components/chat/tools/batch";
import { DocumentRangeSchema, type LinePosition } from "@/lib/types";
import type { SyncOutput } from "@/lib/chat/tool-part";

/** Each read is a DocumentRange under its own path, and spanning the whole file means a full-document fetch. */
export const syncReadDocumentOutput: SyncOutput = ({
  metadata,
  addChunk,
  markFullDocument,
  sourceId,
}) => {
  for (const result of parseBatch(metadata, DocumentRangeSchema).results) {
    if (!result.content) continue;

    const filename = result.file_path;
    const isFullFile = result.start_line === 1 && result.end_line === result.total_lines;

    if (isFullFile) {
      markFullDocument(filename, result.content, "read", sourceId);
      continue;
    }

    const position: LinePosition = {
      type: "line_range",
      startLine: result.start_line,
      endLine: result.end_line,
    };
    addChunk(
      { filename, content: result.content, origin: "read", position, sourceId },
      result.total_lines,
    );
  }
};
