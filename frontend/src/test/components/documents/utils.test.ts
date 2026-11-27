import { expect, it } from "vitest";

import { documentReadMap, readSpans } from "@/components/documents/utils";
import type { FetchedChunk } from "@/lib/chunks";

const partialRead: FetchedChunk = {
  id: "r",
  filename: "report.md",
  content: "x",
  origin: "read",
  position: { type: "line_range", startLine: 1, endLine: 40 },
};

it("maps a partial read to its share of the document, and to none when the length is unknown", () => {
  expect(documentReadMap(readSpans([partialRead]), 200)).toEqual([{ start: 0, end: 0.2 }]);
  expect(documentReadMap(readSpans([partialRead]))).toEqual([]);
});
