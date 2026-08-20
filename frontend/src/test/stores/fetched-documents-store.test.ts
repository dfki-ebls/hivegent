import { beforeEach, describe, expect, it } from "vitest";

import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

describe("useFetchedDocumentsStore", () => {
  beforeEach(() => {
    useFetchedDocumentsStore.getState().clearAll();
  });

  describe("addChunk", () => {
    it("adds a chunk and creates its parent document", () => {
      useFetchedDocumentsStore.getState().addChunk({
        filename: "report.md",
        content: "hello",
        origin: "search",
        position: { type: "line", line: 1 },
      });

      const state = useFetchedDocumentsStore.getState();
      expect(state.chunks.size).toBe(1);
      expect(state.documents.size).toBe(1);

      const doc = state.documents.get("report.md");
      expect(doc).toBeDefined();
      expect(doc!.filename).toBe("report.md");
      expect(doc!.chunkIds).toHaveLength(1);
    });

    it("deduplicates chunks with the same id", () => {
      const chunk = {
        filename: "report.md",
        content: "hello",
        origin: "search" as const,
        position: { type: "line" as const, line: 1 },
      };

      useFetchedDocumentsStore.getState().addChunk(chunk);
      useFetchedDocumentsStore.getState().addChunk(chunk);

      const state = useFetchedDocumentsStore.getState();
      expect(state.chunks.size).toBe(1);
    });

    it("records the document length from a partial read's total_lines", () => {
      useFetchedDocumentsStore.getState().addChunk(
        {
          filename: "report.md",
          content: "head",
          origin: "read",
          position: { type: "line_range", startLine: 1, endLine: 40 },
        },
        200,
      );

      expect(useFetchedDocumentsStore.getState().documents.get("report.md")!.totalLines).toBe(200);
    });

    it("appends a second chunk to the same document", () => {
      useFetchedDocumentsStore.getState().addChunk({
        filename: "report.md",
        content: "a",
        origin: "search",
        position: { type: "line", line: 1 },
      });
      useFetchedDocumentsStore.getState().addChunk({
        filename: "report.md",
        content: "b",
        origin: "search",
        position: { type: "line", line: 2 },
      });

      const state = useFetchedDocumentsStore.getState();
      expect(state.documents.size).toBe(1);
      expect(state.documents.get("report.md")!.chunkIds).toHaveLength(2);
    });
  });

  describe("markFullDocument", () => {
    it("creates a document marked as fully fetched", () => {
      useFetchedDocumentsStore.getState().markFullDocument("report.md", "full content", "read");

      const state = useFetchedDocumentsStore.getState();
      const doc = state.documents.get("report.md");
      expect(doc).toBeDefined();
      expect(doc!.fullContentFetched).toBe(true);
      expect(doc!.fullContent).toBe("full content");
    });

    it("records the line count from the fetched full content", () => {
      useFetchedDocumentsStore.getState().markFullDocument("report.md", "a\nb\nc", "read");

      expect(useFetchedDocumentsStore.getState().documents.get("report.md")!.totalLines).toBe(3);
    });

    it("marks an existing document as fully fetched", () => {
      useFetchedDocumentsStore.getState().addChunk({
        filename: "report.md",
        content: "chunk",
        origin: "search",
        position: { type: "line", line: 1 },
      });
      useFetchedDocumentsStore.getState().markFullDocument("report.md", "full", "read");

      const doc = useFetchedDocumentsStore.getState().documents.get("report.md");
      expect(doc!.fullContentFetched).toBe(true);
      expect(doc!.chunkIds.length).toBeGreaterThanOrEqual(2);
    });

    it("returns identical state references when called with unchanged args", () => {
      useFetchedDocumentsStore.getState().markFullDocument("report.md", "full", "read");
      const before = useFetchedDocumentsStore.getState();

      useFetchedDocumentsStore.getState().markFullDocument("report.md", "full", "read");
      const after = useFetchedDocumentsStore.getState();

      expect(after.documents).toBe(before.documents);
      expect(after.chunks).toBe(before.chunks);
    });
  });

  describe("tool evidence", () => {
    it("keeps repeated reads from distinct tool calls", () => {
      const store = useFetchedDocumentsStore.getState();
      const base = {
        filename: "report.md",
        origin: "read" as const,
        position: { type: "line" as const, line: 1 },
      };

      store.addChunk({ ...base, content: "before", sourceId: "call-1" });
      store.addChunk({ ...base, content: "after", sourceId: "call-2" });

      expect(useFetchedDocumentsStore.getState().chunks.size).toBe(2);
    });
  });

  describe("clearAll", () => {
    it("resets both maps", () => {
      useFetchedDocumentsStore.getState().addChunk({
        filename: "f.md",
        content: "c",
        origin: "read",
        position: { type: "line", line: 1 },
      });
      useFetchedDocumentsStore.getState().clearAll();

      const state = useFetchedDocumentsStore.getState();
      expect(state.chunks.size).toBe(0);
      expect(state.documents.size).toBe(0);
    });
  });
});
