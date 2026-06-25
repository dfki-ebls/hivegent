import { describe, expect, it } from "vitest";

import {
  type ChunkPosition,
  type FetchedChunk,
  chunkSortKey,
  makeChunkId,
  sortChunks,
} from "@/lib/types";

describe("makeChunkId", () => {
  it("builds id for line position", () => {
    const pos: ChunkPosition = { type: "line", line: 42 };
    expect(makeChunkId("file.md", "grep", undefined, pos)).toBe("file.md::grep::line_42");
  });

  it("folds the detail into the id so distinct queries stay separate", () => {
    const pos: ChunkPosition = { type: "line", line: 42 };
    expect(makeChunkId("file.md", "grep", "foo", pos)).toBe("file.md::grep:foo::line_42");
  });

  it("builds id for line_range position", () => {
    const pos: ChunkPosition = {
      type: "line_range",
      startLine: 10,
      endLine: 20,
    };
    expect(makeChunkId("file.md", "search", undefined, pos)).toBe("file.md::search::lines_10_20");
  });

  it("builds id for full_document position", () => {
    const pos: ChunkPosition = { type: "full_document" };
    expect(makeChunkId("file.md", "read", undefined, pos)).toBe("file.md::read::full");
  });
});

describe("chunkSortKey", () => {
  it("returns -1 for full_document", () => {
    expect(chunkSortKey({ type: "full_document" })).toBe(-1);
  });

  it("returns line for line", () => {
    expect(chunkSortKey({ type: "line", line: 42 })).toBe(42);
  });

  it("returns startLine for line_range", () => {
    expect(chunkSortKey({ type: "line_range", startLine: 10, endLine: 20 })).toBe(10);
  });
});

describe("sortChunks", () => {
  const chunk = (id: string, position: ChunkPosition): FetchedChunk => ({
    id,
    filename: "f.md",
    content: "",
    origin: "search",
    position,
  });

  it("sorts full_document first, then by position", () => {
    const chunks = [
      chunk("a", { type: "line", line: 20 }),
      chunk("b", { type: "full_document" }),
      chunk("c", { type: "line", line: 5 }),
    ];

    const sorted = sortChunks(chunks);
    expect(sorted[0].id).toBe("b"); // full_document first
    expect(sorted[1].id).toBe("c"); // line 5
    expect(sorted[2].id).toBe("a"); // line 20
  });

  it("does not mutate the original array", () => {
    const chunks = [chunk("a", { type: "line", line: 10 }), chunk("b", { type: "line", line: 5 })];
    const sorted = sortChunks(chunks);
    expect(chunks[0].id).toBe("a"); // original unchanged
    expect(sorted[0].id).toBe("b");
  });
});
