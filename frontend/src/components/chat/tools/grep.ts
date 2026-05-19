import type { ChunkPosition, GrepMatch } from "@/lib/types";
import type { SyncOutput } from "@/lib/chat/tool-part";

export const syncGrepOutput: SyncOutput = (input, _text, metadata, addChunk) => {
  if (!input) return;
  if (!Array.isArray(metadata)) return;
  const matches = metadata as GrepMatch[];
  const pattern = input.pattern as string;
  if (!matches.length || !pattern) return;

  const source = `grep: ${pattern}`;
  for (const match of matches) {
    if (match.lines.length === 0) continue;
    const startLine = match.lines[0].line_number;
    const endLine = match.lines[match.lines.length - 1].line_number;
    if (startLine <= 0) continue;

    const position: ChunkPosition =
      match.lines.length > 1
        ? { type: "line_range", startLine, endLine }
        : { type: "line", line: startLine };
    addChunk({
      filename: match.filename,
      content: match.lines.map((l) => l.text).join("\n"),
      source,
      position,
    });
  }
};
