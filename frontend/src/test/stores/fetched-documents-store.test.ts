import { beforeEach, expect, it } from "vitest";

import type { FetchedChunk } from "@/lib/chunks";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

type NewChunk = Omit<FetchedChunk, "id">;

const store = () => useFetchedDocumentsStore.getState();
const lineChunk = (line: number, extra: Partial<NewChunk> = {}): NewChunk => ({
  filename: "report.md",
  content: `line ${line}`,
  origin: "search",
  position: { type: "line", line },
  ...extra,
});

beforeEach(() => store().clearAll());

it("groups chunks under their document and deduplicates identical ones", () => {
  store().addChunk(lineChunk(1));
  store().addChunk(lineChunk(1));
  store().addChunk(lineChunk(2));

  expect(store().chunks.size).toBe(2);
  expect(store().documents.size).toBe(1);
  expect(store().documents.get("report.md")?.chunkIds).toHaveLength(2);
});

it("keeps the same read of distinct tool calls apart", () => {
  store().addChunk(lineChunk(1, { origin: "read", sourceId: "call-1" }));
  store().addChunk(lineChunk(1, { origin: "read", sourceId: "call-2" }));

  expect(store().chunks.size).toBe(2);
});

it("records the document length a read or a full fetch reports", () => {
  const range: NewChunk["position"] = { type: "line_range", startLine: 1, endLine: 40 };
  store().addChunk(lineChunk(1, { origin: "read", position: range }), 200);
  store().markFullDocument("full.md", "a\nb\nc\n", "read", "s1", 3);

  expect(store().documents.get("report.md")?.totalLines).toBe(200);
  expect(store().documents.get("full.md")).toMatchObject({
    fullContent: "a\nb\nc\n",
    totalLines: 3,
  });
});

it("marks a document with chunks as fully fetched, and an unchanged mark changes nothing", () => {
  store().addChunk(lineChunk(1));
  store().markFullDocument("report.md", "full", "read");
  const before = store();
  store().markFullDocument("report.md", "full", "read");

  expect(store().documents.get("report.md")?.fullContent).toBe("full");
  expect(store().documents.get("report.md")?.chunkIds.length).toBeGreaterThanOrEqual(2);
  expect(store().documents).toBe(before.documents);
  expect(store().chunks).toBe(before.chunks);
});

it("claims a payload once until the store is cleared", () => {
  const payload = [{ file_path: "a.md" }];

  expect(store().claimOutput(payload)).toBe(true);
  expect(store().claimOutput(payload)).toBe(false);

  store().clearAll();

  expect(store().claimOutput(payload)).toBe(true);
});
