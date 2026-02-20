import { create } from "zustand";
import {
  type ChunkPosition,
  type FetchedChunk,
  type FetchedDocument,
  makeChunkId,
} from "../lib/types";

interface FetchedDocumentsStore {
  chunks: Map<string, FetchedChunk>;
  documents: Map<string, FetchedDocument>;

  /** Insert a chunk, creating/updating its parent document entry. */
  addChunk: (chunk: Omit<FetchedChunk, "id">) => void;

  /** Mark a document as fully fetched, storing its full content. */
  markFullDocument: (
    filename: string,
    content: string,
    source: string,
  ) => void;

  /** Reset both maps. */
  clearAll: () => void;
}

export const useFetchedDocumentsStore = create<FetchedDocumentsStore>(
  (set) => ({
    chunks: new Map(),
    documents: new Map(),

    addChunk: (chunk) =>
      set((state) => {
        const id = makeChunkId(chunk.filename, chunk.source, chunk.position);

        // Deduplicate: skip if we already have this exact chunk
        if (state.chunks.has(id)) return state;

        const newChunks = new Map(state.chunks);
        newChunks.set(id, { ...chunk, id });

        const newDocs = new Map(state.documents);
        const existing = newDocs.get(chunk.filename);

        if (existing) {
          const newBest =
            chunk.score != null
              ? Math.max(chunk.score, existing.bestScore ?? 0)
              : existing.bestScore;
          newDocs.set(chunk.filename, {
            ...existing,
            chunkIds: [...existing.chunkIds, id],
            bestScore: newBest,
          });
        } else {
          newDocs.set(chunk.filename, {
            filename: chunk.filename,
            fullContentFetched: false,
            chunkIds: [id],
            bestScore: chunk.score,
          });
        }

        return { chunks: newChunks, documents: newDocs };
      }),

    markFullDocument: (filename, content, source) =>
      set((state) => {
        const newDocs = new Map(state.documents);
        const newChunks = new Map(state.chunks);

        const position: ChunkPosition = { type: "full_document" };
        const id = makeChunkId(filename, source, position);

        if (!newChunks.has(id)) {
          newChunks.set(id, {
            id,
            filename,
            content,
            source,
            position,
          });
        }

        const existing = newDocs.get(filename);
        if (existing) {
          const chunkIds = existing.chunkIds.includes(id)
            ? existing.chunkIds
            : [...existing.chunkIds, id];
          newDocs.set(filename, {
            ...existing,
            fullContentFetched: true,
            fullContent: content,
            chunkIds,
          });
        } else {
          newDocs.set(filename, {
            filename,
            fullContentFetched: true,
            fullContent: content,
            chunkIds: [id],
          });
        }

        return { chunks: newChunks, documents: newDocs };
      }),

    clearAll: () => set({ chunks: new Map(), documents: new Map() }),
  }),
);
