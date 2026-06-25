import { describe, expect, it } from "vitest";

import { documentReadMap } from "@/components/documents/utils";
import type { FetchedChunk } from "@/lib/types";

const readChunk = (startLine: number, endLine: number): FetchedChunk => ({
  id: `r:${startLine}-${endLine}`,
  filename: "report.md",
  content: "x",
  origin: "read",
  position: { type: "line_range", startLine, endLine },
});

describe("documentReadMap", () => {
  it("does not render a partial read as full coverage when the length is known", () => {
    const segments = documentReadMap([readChunk(1, 40)], 200);

    expect(segments).toEqual([{ start: 0, end: 0.2 }]);
  });

  it("falls back to the furthest read line when the length is unknown", () => {
    const segments = documentReadMap([readChunk(1, 40)]);

    expect(segments).toEqual([{ start: 0, end: 1 }]);
  });
});
