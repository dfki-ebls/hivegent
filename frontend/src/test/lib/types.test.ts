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
    expect(makeChunkId("file.md", "grep", pos)).toBe("file.md::grep::line_42");
  });

  it("builds id for line_range position", () => {
    const pos: ChunkPosition = {
      type: "line_range",
      startLine: 10,
      endLine: 20,
    };
    expect(makeChunkId("file.md", "range", pos)).toBe("file.md::range::lines_10_20");
  });

  it("builds id for full_document position", () => {
    const pos: ChunkPosition = { type: "full_document" };
    expect(makeChunkId("file.md", "fetch", pos)).toBe("file.md::fetch::full");
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
  it("sorts full_document first, then by position", () => {
    const chunks: FetchedChunk[] = [
      {
        id: "a",
        filename: "f.md",
        content: "",
        source: "s",
        position: { type: "line", line: 20 },
      },
      {
        id: "b",
        filename: "f.md",
        content: "",
        source: "s",
        position: { type: "full_document" },
      },
      {
        id: "c",
        filename: "f.md",
        content: "",
        source: "s",
        position: { type: "line", line: 5 },
      },
    ];

    const sorted = sortChunks(chunks);
    expect(sorted[0].id).toBe("b"); // full_document first
    expect(sorted[1].id).toBe("c"); // line 5
    expect(sorted[2].id).toBe("a"); // line 20
  });

  it("does not mutate the original array", () => {
    const chunks: FetchedChunk[] = [
      {
        id: "a",
        filename: "f.md",
        content: "",
        source: "s",
        position: { type: "line", line: 10 },
      },
      {
        id: "b",
        filename: "f.md",
        content: "",
        source: "s",
        position: { type: "line", line: 5 },
      },
    ];
    const sorted = sortChunks(chunks);
    expect(chunks[0].id).toBe("a"); // original unchanged
    expect(sorted[0].id).toBe("b");
  });
});
