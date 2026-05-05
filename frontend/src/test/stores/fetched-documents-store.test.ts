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
        source: "search",
        position: { type: "line", line: 1 },
        score: 0.9,
      });

      const state = useFetchedDocumentsStore.getState();
      expect(state.chunks.size).toBe(1);
      expect(state.documents.size).toBe(1);

      const doc = state.documents.get("report.md");
      expect(doc).toBeDefined();
      expect(doc!.filename).toBe("report.md");
      expect(doc!.chunkIds).toHaveLength(1);
      expect(doc!.bestScore).toBe(0.9);
    });

    it("deduplicates chunks with the same id", () => {
      const chunk = {
        filename: "report.md",
        content: "hello",
        source: "search",
        position: { type: "line" as const, line: 1 },
      };

      useFetchedDocumentsStore.getState().addChunk(chunk);
      useFetchedDocumentsStore.getState().addChunk(chunk);

      const state = useFetchedDocumentsStore.getState();
      expect(state.chunks.size).toBe(1);
    });

    it("updates bestScore on second chunk for same document", () => {
      useFetchedDocumentsStore.getState().addChunk({
        filename: "report.md",
        content: "a",
        source: "search",
        position: { type: "line", line: 1 },
        score: 0.5,
      });
      useFetchedDocumentsStore.getState().addChunk({
        filename: "report.md",
        content: "b",
        source: "search",
        position: { type: "line", line: 2 },
        score: 0.9,
      });

      const doc = useFetchedDocumentsStore.getState().documents.get("report.md");
      expect(doc!.bestScore).toBe(0.9);
      expect(doc!.chunkIds).toHaveLength(2);
    });
  });

  describe("markFullDocument", () => {
    it("creates a document marked as fully fetched", () => {
      useFetchedDocumentsStore.getState().markFullDocument("report.md", "full content", "fetch");

      const state = useFetchedDocumentsStore.getState();
      const doc = state.documents.get("report.md");
      expect(doc).toBeDefined();
      expect(doc!.fullContentFetched).toBe(true);
      expect(doc!.fullContent).toBe("full content");
    });

    it("marks an existing document as fully fetched", () => {
      useFetchedDocumentsStore.getState().addChunk({
        filename: "report.md",
        content: "chunk",
        source: "search",
        position: { type: "line", line: 1 },
      });
      useFetchedDocumentsStore.getState().markFullDocument("report.md", "full", "fetch");

      const doc = useFetchedDocumentsStore.getState().documents.get("report.md");
      expect(doc!.fullContentFetched).toBe(true);
      expect(doc!.chunkIds.length).toBeGreaterThanOrEqual(2);
    });

    it("returns identical state references when called with unchanged args", () => {
      useFetchedDocumentsStore.getState().markFullDocument("report.md", "full", "fetch");
      const before = useFetchedDocumentsStore.getState();

      useFetchedDocumentsStore.getState().markFullDocument("report.md", "full", "fetch");
      const after = useFetchedDocumentsStore.getState();

      expect(after.documents).toBe(before.documents);
      expect(after.chunks).toBe(before.chunks);
    });
  });

  describe("clearAll", () => {
    it("resets both maps", () => {
      useFetchedDocumentsStore.getState().addChunk({
        filename: "f.md",
        content: "c",
        source: "s",
        position: { type: "line", line: 1 },
      });
      useFetchedDocumentsStore.getState().clearAll();

      const state = useFetchedDocumentsStore.getState();
      expect(state.chunks.size).toBe(0);
      expect(state.documents.size).toBe(0);
    });
  });
});
