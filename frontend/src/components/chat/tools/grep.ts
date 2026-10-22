import { z } from "zod";
import { type ChunkPosition, GrepMatchSchema } from "@/lib/types";
import type { SyncOutput } from "@/lib/chat/tool-part";

/** The match blocks of every output mode, which the counting modes search without context. */
const GrepMatchesSchema = z.array(GrepMatchSchema);

export const syncGrepOutput: SyncOutput = ({ input, metadata, addChunk, sourceId }) => {
  if (!input) return;
  const matches = GrepMatchesSchema.safeParse(metadata).data;
  const pattern = input.pattern as string;
  if (!matches?.length || !pattern) return;

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
      origin: "grep",
      detail: pattern,
      position,
      sourceId,
    });
  }
};
