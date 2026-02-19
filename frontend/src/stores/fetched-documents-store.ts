import { create } from "zustand";
import type { RetrievedDocument, StoredDocument } from "../lib/types";

interface FetchedDocumentsStore {
  documents: Map<string, StoredDocument>;
  addSearchResults: (docs: RetrievedDocument[], query: string) => void;
  addDocument: (filename: string, content: string, source: string) => void;
  addDocumentReference: (
    filename: string,
    sources: string[],
    score?: number,
  ) => void;
  clearDocuments: () => void;
}

export const useFetchedDocumentsStore = create<FetchedDocumentsStore>(
  (set) => ({
    documents: new Map(),
    addSearchResults: (docs, query) =>
      set((state) => {
        const newMap = new Map(state.documents);
        const source = `search: ${query}`;
        for (const doc of docs) {
          const existing = newMap.get(doc.filename);
          if (existing) {
            const sources = existing.sources.includes(source)
              ? existing.sources
              : [...existing.sources, source];
            const newScore = Math.max(doc.score, existing.score ?? 0);
            newMap.set(doc.filename, { ...existing, score: newScore, sources });
          } else {
            newMap.set(doc.filename, {
              filename: doc.filename,
              content: doc.content,
              score: doc.score,
              sources: [source],
            });
          }
        }
        return { documents: newMap };
      }),
    addDocument: (filename, content, source) =>
      set((state) => {
        const newMap = new Map(state.documents);
        const existing = newMap.get(filename);
        if (existing) {
          const sources = existing.sources.includes(source)
            ? existing.sources
            : [...existing.sources, source];
          // Keep existing content if it's longer (more complete)
          const newContent =
            content.length > existing.content.length
              ? content
              : existing.content;
          newMap.set(filename, { ...existing, content: newContent, sources });
        } else {
          newMap.set(filename, { filename, content, sources: [source] });
        }
        return { documents: newMap };
      }),
    addDocumentReference: (filename, sources, score) =>
      set((state) => {
        const newMap = new Map(state.documents);
        // Set the reference (content will be fetched when expanded)
        newMap.set(filename, { filename, content: "", sources, score });
        return { documents: newMap };
      }),
    clearDocuments: () => set({ documents: new Map() }),
  }),
);
