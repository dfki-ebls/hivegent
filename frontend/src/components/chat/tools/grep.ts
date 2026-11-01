import { z } from "zod";
import type { ChunkPosition } from "@/lib/chunks";
import { GrepMatchSchema } from "@/lib/chat/tool-outputs";
import type { SyncOutput } from "@/lib/chat/tool-part";

/** The match blocks of content mode, and the listed files without lines in the counting modes. */
const GrepMatchesSchema = z.array(GrepMatchSchema);

export const syncGrepOutput: SyncOutput = ({ input, metadata, addChunk, sourceId }) => {
  if (!input) return;
  const matches = GrepMatchesSchema.safeParse(metadata).data;
  const pattern = input.pattern as string;
  if (!matches?.length || !pattern) return;

  for (const match of matches) {
    const first = match.lines[0];
    const last = match.lines.at(-1);

    if (!first || !last || first.line_number <= 0) continue;

    const startLine = first.line_number;
    const endLine = last.line_number;

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
