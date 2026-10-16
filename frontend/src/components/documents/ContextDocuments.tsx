import { Search } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useDialogTarget } from "@/hooks/use-dialog-target";
import { useDocumentLineCounts } from "@/hooks/use-document-line-counts";
import type { FetchedChunk, FetchedDocument } from "@/lib/types";
import { chunksForDocument, useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { DocumentDialog } from "@/components/DocumentDialog";
import { EmptyState } from "@/components/EmptyState";
import { ScrollArea } from "@/components/ui/scroll-area";
import { DocumentGroup } from "@/components/documents/DocumentGroup";

/** The document the dialog opens on, anchored on a chunk when it has one. */
interface DialogTarget {
  filename: string;
  chunk: FetchedChunk | null;
  initialFullDoc: boolean;
}

export function ContextDocuments() {
  const { t } = useTranslation();
  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);

  // Lazily backfill line counts for search/grep docs so their map renders.
  useDocumentLineCounts();

  const dialog = useDialogTarget<DialogTarget>({
    filename: "",
    chunk: null,
    initialFullDoc: false,
  });

  // The dialog's image is just the open document's image, so derive it rather
  // than tracking a parallel piece of state.
  const dialogImage = documents.get(dialog.target.filename)?.image ?? null;

  // Surface the most relevant documents first: most chunks wins, filename
  // breaks ties. `chunkIds` is the count itself, since the store only ever
  // appends an id alongside the chunk it names. Chunks within each group stay
  // line-ordered via sortChunks in DocumentGroup.
  const sortedDocs = useMemo(
    () =>
      Array.from(documents.values()).sort(
        (a, b) => b.chunkIds.length - a.chunkIds.length || a.filename.localeCompare(b.filename),
      ),
    [documents],
  );

  const handleChunkClick = (chunk: FetchedChunk) =>
    dialog.show({
      filename: chunk.filename,
      chunk,
      initialFullDoc: chunk.position.type === "full_document",
    });

  // Opens on the first chunk so the sidebar has an anchor, or on the whole document without one.
  const handleFilenameClick = (filename: string) => {
    const doc = documents.get(filename);
    const chunk = (doc && chunks.get(doc.chunkIds[0])) ?? null;

    dialog.show({ filename, chunk, initialFullDoc: true });
  };

  const handleImageClick = (doc: FetchedDocument) =>
    dialog.show({ filename: doc.filename, chunk: null, initialFullDoc: true });

  if (sortedDocs.length === 0) {
    return (
      <EmptyState
        icon={<Search />}
        title={t(($) => $.documents.context.emptyTitle)}
        description={t(($) => $.documents.context.emptyDescription)}
        className="h-full"
      />
    );
  }

  return (
    <>
      <ScrollArea className="h-full">
        <div className="flex flex-col gap-1 p-4">
          {sortedDocs.map((doc) => (
            <DocumentGroup
              key={doc.filename}
              doc={doc}
              chunks={chunksForDocument(doc, chunks)}
              onChunkClick={handleChunkClick}
              onFilenameClick={handleFilenameClick}
              onImageClick={handleImageClick}
            />
          ))}
        </div>
      </ScrollArea>

      <DocumentDialog
        open={dialog.open}
        onOpenChange={dialog.setOpen}
        filename={dialog.target.filename}
        chunk={dialog.target.chunk}
        image={dialogImage}
        initialFullDoc={dialog.target.initialFullDoc}
      />
    </>
  );
}
