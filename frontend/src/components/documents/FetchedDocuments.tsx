import { Search } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import type { FetchedChunk, FetchedDocument } from "../../lib/types";
import { useFetchedDocumentsStore } from "../../stores/fetched-documents-store";
import { DocumentDialog } from "../DocumentDialog";
import { ScrollArea } from "../ui/scroll-area";
import { DocumentGroup } from "./DocumentGroup";
import { EmptyState } from "./EmptyState";

export function FetchedDocuments() {
  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);

  // Dialog state
  const [selectedChunk, setSelectedChunk] = useState<FetchedChunk | null>(null);
  const [dialogFilename, setDialogFilename] = useState<string | undefined>(undefined);
  const [initialFullDoc, setInitialFullDoc] = useState(false);
  const dialogOpen = selectedChunk !== null || dialogFilename !== undefined;

  // Order documents lexically by filename; chunks within each group stay
  // line-ordered via sortChunks in DocumentGroup.
  const sortedDocs = useMemo(
    () => Array.from(documents.values()).sort((a, b) => a.filename.localeCompare(b.filename)),
    [documents],
  );

  const getChunksForDoc = useCallback(
    (doc: FetchedDocument): FetchedChunk[] =>
      doc.chunkIds.map((id) => chunks.get(id)).filter((c): c is FetchedChunk => c != null),
    [chunks],
  );

  // Open the dialog on a specific chunk (in chunk-context mode)
  const handleChunkClick = useCallback((chunk: FetchedChunk) => {
    setInitialFullDoc(chunk.position.type === "full_document");
    setDialogFilename(undefined);
    setSelectedChunk(chunk);
  }, []);

  // Open the dialog for a document (in full-document mode)
  const handleFilenameClick = useCallback(
    (filename: string) => {
      setInitialFullDoc(true);
      // Try to pick the first chunk as anchor for the sidebar
      const doc = documents.get(filename);
      const first = doc && doc.chunkIds.length > 0 ? chunks.get(doc.chunkIds[0]) : null;
      if (first) {
        setDialogFilename(undefined);
        setSelectedChunk(first);
      } else {
        // No chunks yet — use fallbackFilename
        setSelectedChunk(null);
        setDialogFilename(filename);
      }
    },
    [documents, chunks],
  );

  const closeDialog = useCallback(() => {
    setSelectedChunk(null);
    setDialogFilename(undefined);
  }, []);

  if (sortedDocs.length === 0) {
    return (
      <EmptyState
        icon={<Search className="h-12 w-12 opacity-50" />}
        title="Fetched documents will appear here"
        description="Ask questions in the chat to search and fetch documents"
      />
    );
  }

  return (
    <>
      <ScrollArea className="h-full">
        <div className="space-y-1 p-4">
          {sortedDocs.map((doc) => (
            <DocumentGroup
              key={doc.filename}
              doc={doc}
              chunks={getChunksForDoc(doc)}
              onChunkClick={handleChunkClick}
              onFilenameClick={handleFilenameClick}
            />
          ))}
        </div>
      </ScrollArea>

      <DocumentDialog
        open={dialogOpen}
        onOpenChange={(v) => !v && closeDialog()}
        filename={selectedChunk?.filename ?? dialogFilename ?? ""}
        chunk={selectedChunk}
        fallbackFilename={dialogFilename}
        initialFullDoc={initialFullDoc}
      />
    </>
  );
}
