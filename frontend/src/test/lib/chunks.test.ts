import { expect, it } from "vitest";

import { type ChunkPosition, type FetchedChunk, makeChunkId, sortChunks } from "@/lib/chunks";

it.each<[ChunkPosition, string | undefined, string]>([
  [{ type: "line", line: 42 }, undefined, "file.md::grep::line_42"],
  [{ type: "line", line: 42 }, "foo", "file.md::grep:foo::line_42"],
  [{ type: "line_range", startLine: 10, endLine: 20 }, undefined, "file.md::grep::lines_10_20"],
  [{ type: "full_document" }, undefined, "file.md::grep::full"],
])("names the chunk at %j with detail %j", (position, detail, id) => {
  expect(makeChunkId("file.md", "grep", detail, position)).toBe(id);
});

it("sorts the full document first, then by line, without mutating its input", () => {
  const chunk = (id: string, position: ChunkPosition): FetchedChunk => ({
    id,
    filename: "f.md",
    content: "",
    origin: "search",
    position,
  });
  const chunks = [
    chunk("a", { type: "line", line: 20 }),
    chunk("b", { type: "full_document" }),
    chunk("c", { type: "line_range", startLine: 5, endLine: 9 }),
  ];

  expect(sortChunks(chunks).map((c) => c.id)).toEqual(["b", "c", "a"]);
  expect(chunks.map((c) => c.id)).toEqual(["a", "b", "c"]);
});
