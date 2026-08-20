import { create } from "zustand";
import {
  type ChunkOrigin,
  type ChunkPosition,
  type FetchedChunk,
  type FetchedDocument,
  type FetchedImage,
  makeChunkId,
} from "@/lib/types";

export interface FetchedDocumentsStore {
  chunks: Map<string, FetchedChunk>;
  documents: Map<string, FetchedDocument>;

  /**
   * Insert a chunk, creating/updating its parent document entry.  Pass
   * *totalLines* when the source knows the document's length (a partial read)
   * so the coverage map has a denominator before full content is fetched.
   */
  addChunk: (chunk: Omit<FetchedChunk, "id">, totalLines?: number) => void;

  /** Mark a document as fully fetched, storing its full content. */
  markFullDocument: (
    filename: string,
    content: string,
    origin: ChunkOrigin,
    sourceId?: string,
  ) => void;

  /** Record fetched document line counts (the coverage-map denominator). */
  setLineCounts: (counts: Record<string, number>) => void;

  /** Attach an image to the document at *filename* (its description path). */
  addImage: (filename: string, image: FetchedImage) => void;

  /** Reset both maps. */
  clearAll: () => void;
}

/** The store callbacks the tool-output sync hands to each tool handler. */
export type AddChunk = FetchedDocumentsStore["addChunk"];
export type MarkFullDocument = FetchedDocumentsStore["markFullDocument"];
export type AddImage = FetchedDocumentsStore["addImage"];

/** Resolve a document's chunks through the `chunkIds` index the store maintains. */
export function chunksForDocument(
  document: FetchedDocument,
  chunks: ReadonlyMap<string, FetchedChunk>,
): FetchedChunk[] {
  return document.chunkIds
    .map((id) => chunks.get(id))
    .filter((chunk): chunk is FetchedChunk => chunk != null);
}

export const useFetchedDocumentsStore = create<FetchedDocumentsStore>((set) => ({
  chunks: new Map(),
  documents: new Map(),

  addChunk: (chunk, totalLines) =>
    set((state) => {
      const id = makeChunkId(
        chunk.filename,
        chunk.origin,
        chunk.detail,
        chunk.position,
        chunk.sourceId,
      );

      // Deduplicate: skip if we already have this exact chunk
      if (state.chunks.has(id)) return state;

      const newChunks = new Map(state.chunks);
      newChunks.set(id, { ...chunk, id });

      const newDocs = new Map(state.documents);
      const existing = newDocs.get(chunk.filename);

      if (existing) {
        newDocs.set(chunk.filename, {
          ...existing,
          totalLines: totalLines ?? existing.totalLines,
          chunkIds: [...existing.chunkIds, id],
        });
      } else {
        newDocs.set(chunk.filename, {
          filename: chunk.filename,
          fullContentFetched: false,
          totalLines,
          chunkIds: [id],
        });
      }

      return { chunks: newChunks, documents: newDocs };
    }),

  markFullDocument: (filename, content, origin, sourceId) =>
    set((state) => {
      const position: ChunkPosition = { type: "full_document" };
      const id = makeChunkId(filename, origin, undefined, position, sourceId);

      const existingChunk = state.chunks.get(id);
      const existingDoc = state.documents.get(filename);
      const chunkUpToDate = existingChunk?.content === content;
      const docUpToDate =
        existingDoc?.fullContentFetched === true &&
        existingDoc.fullContent === content &&
        existingDoc.chunkIds.includes(id);

      if (chunkUpToDate && docUpToDate) return state;

      const newChunks = chunkUpToDate ? state.chunks : new Map(state.chunks);
      if (!chunkUpToDate) {
        newChunks.set(id, { id, filename, content, origin, position, sourceId });
      }

      const newDocs = docUpToDate ? state.documents : new Map(state.documents);
      if (!docUpToDate) {
        const totalLines = content.split("\n").length;
        if (existingDoc) {
          const chunkIds = existingDoc.chunkIds.includes(id)
            ? existingDoc.chunkIds
            : [...existingDoc.chunkIds, id];
          newDocs.set(filename, {
            ...existingDoc,
            fullContentFetched: true,
            fullContent: content,
            totalLines,
            chunkIds,
          });
        } else {
          newDocs.set(filename, {
            filename,
            fullContentFetched: true,
            fullContent: content,
            totalLines,
            chunkIds: [id],
          });
        }
      }

      return { chunks: newChunks, documents: newDocs };
    }),

  setLineCounts: (counts) =>
    set((state) => {
      let changed = false;
      const newDocs = new Map(state.documents);

      for (const [filename, lineCount] of Object.entries(counts)) {
        const doc = newDocs.get(filename);
        if (doc && doc.totalLines !== lineCount) {
          newDocs.set(filename, { ...doc, totalLines: lineCount });
          changed = true;
        }
      }

      return changed ? { documents: newDocs } : state;
    }),

  addImage: (filename, image) =>
    set((state) => {
      const existing = state.documents.get(filename);
      if (existing?.image?.filePath === image.filePath) return state;

      const newDocs = new Map(state.documents);
      newDocs.set(
        filename,
        existing
          ? { ...existing, image }
          : { filename, fullContentFetched: false, chunkIds: [], image },
      );
      return { documents: newDocs };
    }),

  clearAll: () => set({ chunks: new Map(), documents: new Map() }),
}));
