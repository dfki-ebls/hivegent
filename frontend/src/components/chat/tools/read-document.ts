import type { DocumentRange, LinePosition } from "@/lib/types";
import type { SyncOutput } from "@/lib/chat/tool-part";

/** Reads always return a DocumentRange; spanning the whole file means a full-document fetch. */
export const syncReadDocumentOutput: SyncOutput = (
  input,
  _text,
  metadata,
  addChunk,
  markFullDocument,
) => {
  if (!input) return;
  const filename = input.file_path as string;
  if (!filename) return;
  if (metadata == null || typeof metadata !== "object") return;
  if (!("start_line" in (metadata as object))) return;
  const result = metadata as DocumentRange;
  if (!result.content) return;

  const isFullFile = result.start_line === 1 && result.end_line === result.total_lines;
  if (isFullFile) {
    markFullDocument(filename, result.content, "read_document");
    return;
  }

  const position: LinePosition = {
    type: "line_range",
    startLine: result.start_line,
    endLine: result.end_line,
  };
  addChunk({ filename, content: result.content, source: "read", position });
};
