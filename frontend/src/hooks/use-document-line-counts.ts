import { useEffect, useRef } from "react";

import { getDocumentLineCounts } from "@/lib/api";
import { featureFlags } from "@/lib/feature-flags";
import { isLinePosition } from "@/lib/types";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

/**
 * Lazily resolve the line count for context-panel documents that would render a
 * coverage map but whose length is unknown — search/grep hits that were never
 * read or opened.  Each path is requested at most once; documents that already
 * know their length, web URLs, and chunks without line numbers are skipped, so
 * a fresh search costs a single batched request.
 */
export function useDocumentLineCounts(): void {
  const documents = useFetchedDocumentsStore((s) => s.documents);
  const chunks = useFetchedDocumentsStore((s) => s.chunks);
  const setLineCounts = useFetchedDocumentsStore((s) => s.setLineCounts);
  const requested = useRef<Set<string>>(new Set());

  useEffect(() => {
    // The map is the only consumer of these counts, so disabling it skips the
    // fetch entirely.
    if (!featureFlags.documentMap) return;

    // A cleared store (new conversation) lets previously seen paths be re-asked.
    if (documents.size === 0) {
      requested.current.clear();
      return;
    }

    // Web results and unlocated text carry no line-located chunks, so this
    // also excludes them — no separate web-URL check is needed.
    const hasLineChunk = (chunkIds: string[]) =>
      chunkIds.some((id) => {
        const position = chunks.get(id)?.position;
        return position !== undefined && isLinePosition(position);
      });

    const missing = [...documents.values()]
      .filter(
        (doc) =>
          doc.totalLines === undefined &&
          !requested.current.has(doc.filename) &&
          hasLineChunk(doc.chunkIds),
      )
      .map((doc) => doc.filename);

    if (missing.length === 0) return;

    for (const filename of missing) requested.current.add(filename);

    let cancelled = false;
    getDocumentLineCounts(missing)
      .then((counts) => {
        if (!cancelled) setLineCounts(counts);
      })
      .catch(() => {
        // Leave the map hidden on failure; a later read or open still fills it.
      });

    return () => {
      cancelled = true;
    };
  }, [documents, chunks, setLineCounts]);
}
