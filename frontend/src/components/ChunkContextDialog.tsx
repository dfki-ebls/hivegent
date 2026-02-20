import { FileText, Loader2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Streamdown } from "streamdown";

import { getDocumentContent } from "@/lib/api";
import {
  type FetchedChunk,
  chunkPositionLabel,
  sortChunks,
} from "@/lib/types";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { Badge } from "./ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { ScrollArea } from "./ui/scroll-area";

interface ChunkContextDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  chunk: FetchedChunk | null;
  /** Fallback filename when no chunk is available (opens in full-doc mode). */
  fallbackFilename?: string;
  /** When true the dialog opens directly into the full-document markdown view. */
  initialFullDoc?: boolean;
}

export function ChunkContextDialog({
  open,
  onOpenChange,
  chunk,
  fallbackFilename,
  initialFullDoc = false,
}: ChunkContextDialogProps) {
  const [fullContent, setFullContent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showFullDoc, setShowFullDoc] = useState(initialFullDoc);
  const highlightRef = useRef<HTMLDivElement>(null);

  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);
  const markFullDocument = useFetchedDocumentsStore(
    (state) => state.markFullDocument,
  );

  const filename = chunk?.filename ?? fallbackFilename ?? "";

  // Sorted sibling chunks for this document (consistent with canvas)
  const siblingChunks = useMemo(() => {
    if (!filename) return [];
    const doc = documents.get(filename);
    if (!doc) return [];
    const resolved = doc.chunkIds
      .map((id) => chunks.get(id))
      .filter((c): c is FetchedChunk => c != null);
    return sortChunks(resolved);
  }, [filename, documents, chunks]);

  // Active chunk ID for sidebar highlighting
  const [activeChunkId, setActiveChunkId] = useState<string | null>(null);

  // Reset state when the dialog opens with a new chunk
  useEffect(() => {
    if (open) {
      setActiveChunkId(chunk?.id ?? null);
      setShowFullDoc(initialFullDoc || !chunk);
    }
  }, [open, chunk, initialFullDoc]);

  const activeChunk = activeChunkId
    ? (chunks.get(activeChunkId) ?? chunk)
    : chunk;

  // Sync fullContent from store when it becomes available externally
  useEffect(() => {
    const doc = documents.get(filename);
    if (doc?.fullContent) {
      setFullContent(doc.fullContent);
      setIsLoading(false);
    }
  }, [documents, filename]);

  // Fetch full document content on open (reads store directly to avoid dep loop)
  useEffect(() => {
    if (!open || !filename) return;

    const doc = useFetchedDocumentsStore.getState().documents.get(filename);
    if (doc?.fullContent) {
      setFullContent(doc.fullContent);
      return;
    }

    let cancelled = false;
    setIsLoading(true);
    getDocumentContent(filename)
      .then((content) => {
        if (!cancelled) {
          setFullContent(content);
          markFullDocument(filename, content, "preview");
          setIsLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFullContent(null);
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, filename, markFullDocument]);

  // Scroll to highlight when content loads or active chunk changes
  useEffect(() => {
    if (highlightRef.current) {
      highlightRef.current.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }
  }, [activeChunkId, fullContent, showFullDoc]);

  if (!chunk && !fallbackFilename) return null;

  // --- Render helpers ---

  const renderMainContent = () => {
    if (isLoading) {
      return (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      );
    }

    // Full-document markdown view
    if (showFullDoc) {
      if (!fullContent) {
        return (
          <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
            Document content unavailable
          </div>
        );
      }
      return (
        <ScrollArea className="flex-1 min-h-0">
          <div className="prose prose-sm dark:prose-invert max-w-none p-4">
            <Streamdown>{fullContent}</Streamdown>
          </div>
        </ScrollArea>
      );
    }

    // Chunk-in-context view
    if (!activeChunk) return null;

    if (fullContent) {
      const chunkText = activeChunk.content;
      const idx = fullContent.indexOf(chunkText);

      if (idx >= 0) {
        const before = fullContent.slice(0, idx);
        const after = fullContent.slice(idx + chunkText.length);

        return (
          <ScrollArea className="flex-1 min-h-0">
            <pre className="whitespace-pre-wrap text-sm p-4 font-mono">
              <span className="text-muted-foreground">{before}</span>
              <span
                ref={highlightRef}
                className="bg-yellow-200/50 dark:bg-yellow-900/50 border-l-2 border-yellow-500 pl-1"
              >
                {chunkText}
              </span>
              <span className="text-muted-foreground">{after}</span>
            </pre>
          </ScrollArea>
        );
      }
    }

    // Fallback: just show the chunk content
    return (
      <ScrollArea className="flex-1 min-h-0">
        <pre className="whitespace-pre-wrap text-sm p-4 font-mono">
          {activeChunk.content}
        </pre>
      </ScrollArea>
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="h-[85vh] w-[90vw] max-w-5xl! flex flex-col p-0">
        <DialogHeader className="px-6 pt-6 pb-3">
          <DialogTitle className="truncate">{filename}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-1 min-h-0">
          {/* Sidebar */}
          <div className="w-56 shrink-0 border-r flex flex-col">
            <ScrollArea className="flex-1">
              <div className="p-2 space-y-1">
                {/* Full Document toggle */}
                <button
                  type="button"
                  className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors flex items-center gap-1.5 ${
                    showFullDoc
                      ? "bg-accent text-accent-foreground"
                      : "hover:bg-muted"
                  }`}
                  onClick={() => setShowFullDoc(true)}
                >
                  <FileText className="h-3 w-3 shrink-0" />
                  <span className="font-medium">Full document</span>
                </button>

                {siblingChunks.length > 0 && (
                  <div className="border-t my-1" />
                )}

                {/* Chunk entries */}
                {siblingChunks
                  .filter((c) => c.position.type !== "full_document")
                  .map((sibling) => (
                    <button
                      key={sibling.id}
                      type="button"
                      className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors ${
                        !showFullDoc && sibling.id === activeChunkId
                          ? "bg-accent text-accent-foreground"
                          : "hover:bg-muted"
                      }`}
                      onClick={() => {
                        setActiveChunkId(sibling.id);
                        setShowFullDoc(false);
                      }}
                    >
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <Badge
                          variant={
                            !showFullDoc && sibling.id === activeChunkId
                              ? "default"
                              : "outline"
                          }
                          className="text-[10px] shrink-0"
                        >
                          {chunkPositionLabel(sibling.position)}
                        </Badge>
                        {sibling.score != null && (
                          <span className="text-muted-foreground">
                            {(sibling.score * 100).toFixed(0)}%
                          </span>
                        )}
                      </div>
                      <p className="truncate text-muted-foreground mt-0.5">
                        {sibling.content.slice(0, 60)}
                        {sibling.content.length > 60 ? "..." : ""}
                      </p>
                    </button>
                  ))}
              </div>
            </ScrollArea>
          </div>

          {/* Main content area */}
          <div className="flex-1 flex flex-col min-h-0 min-w-0">
            {!showFullDoc && activeChunk && (
              <div className="flex items-center gap-2 px-4 py-2 border-b">
                <Badge variant="outline" className="text-xs">
                  {activeChunk.source}
                </Badge>
                <Badge variant="secondary" className="text-xs">
                  {chunkPositionLabel(activeChunk.position)}
                </Badge>
                {activeChunk.score != null && (
                  <Badge variant="secondary" className="text-xs">
                    {(activeChunk.score * 100).toFixed(0)}%
                  </Badge>
                )}
              </div>
            )}
            {renderMainContent()}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
