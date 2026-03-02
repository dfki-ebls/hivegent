import { FileText, Loader2, Pencil, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Streamdown } from "streamdown";

import { getDocumentChunks, getDocumentContent } from "@/lib/api";
import {
  type ChunkedDocumentResponse,
  type FetchedChunk,
  chunkPositionLabel,
  sortChunks,
} from "@/lib/types";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Input } from "./ui/input";
import { ScrollArea } from "./ui/scroll-area";
import { Textarea } from "./ui/textarea";

interface DocumentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filename: string;

  /** Fetched mode: chunk to highlight, sidebar shows sibling chunks from store. */
  chunk?: FetchedChunk | null;
  /** Fallback filename when no chunk is available (opens in full-doc mode). */
  fallbackFilename?: string;
  /** When true the dialog opens directly into the full-document markdown view. */
  initialFullDoc?: boolean;

  /** Management mode: show pipeline/chunk_size/created_at badges. */
  showMetadata?: boolean;
  /** Management mode: show rechunk button and trigger rechunk. */
  onRechunk?: () => Promise<void>;

  /** Show edit/preview toggle. */
  editable?: boolean;
  /** New document: editable filename, starts in edit mode. */
  isNew?: boolean;
  /** Save handler for edit mode. */
  onSave?: (filename: string, content: string) => Promise<void>;
  /** Custom content fetcher (defaults to getDocumentContent). */
  getContent?: (filename: string) => Promise<string>;
}

type ViewMode = "full-doc" | "chunk" | "edit";

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleString();
}

export function DocumentDialog({
  open,
  onOpenChange,
  filename: filenameProp,
  chunk,
  fallbackFilename,
  initialFullDoc = false,
  showMetadata = false,
  onRechunk,
  editable = false,
  isNew = false,
  onSave,
  getContent,
}: DocumentDialogProps) {
  // --- State ---
  const [fullContent, setFullContent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("full-doc");
  const [activeChunkId, setActiveChunkId] = useState<string | null>(null);
  const highlightRef = useRef<HTMLDivElement>(null);

  // Managed-mode chunk data
  const [managedData, setManagedData] = useState<ChunkedDocumentResponse | null>(null);
  const [managedLoading, setManagedLoading] = useState(false);
  const [managedError, setManagedError] = useState<string | null>(null);
  const [isRechunking, setIsRechunking] = useState(false);
  const [managedActiveIndex, setManagedActiveIndex] = useState<number | null>(null);

  // Editing state
  const [editFilename, setEditFilename] = useState(filenameProp);
  const [editContent, setEditContent] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  // Fetched-mode store access
  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);
  const markFullDocument = useFetchedDocumentsStore((state) => state.markFullDocument);

  const isManagedMode = showMetadata || onRechunk != null;
  const filename = chunk?.filename ?? fallbackFilename ?? filenameProp;

  // --- Fetched-mode sibling chunks ---
  const siblingChunks = useMemo(() => {
    if (isManagedMode || !filename) return [];
    const doc = documents.get(filename);
    if (!doc) return [];
    const resolved = doc.chunkIds
      .map((id) => chunks.get(id))
      .filter((c): c is FetchedChunk => c != null);
    return sortChunks(resolved);
  }, [isManagedMode, filename, documents, chunks]);

  const activeChunk = activeChunkId ? (chunks.get(activeChunkId) ?? chunk) : chunk;

  // --- Reset state on open ---
  useEffect(() => {
    if (!open) return;

    setEditFilename(filenameProp);
    setManagedData(null);
    setManagedError(null);
    setManagedActiveIndex(null);

    if (isNew) {
      setViewMode("edit");
      setEditContent("");
      setFullContent(null);
    } else if (chunk) {
      setActiveChunkId(chunk.id);
      setViewMode(initialFullDoc || chunk.position.type === "full_document" ? "full-doc" : "chunk");
    } else {
      setActiveChunkId(null);
      setViewMode(editable ? "full-doc" : "full-doc");
    }
  }, [open, chunk, initialFullDoc, isNew, filenameProp, editable]);

  // --- Fetch managed chunks ---
  const fetchManagedChunks = useCallback(() => {
    if (!filename) return;
    setManagedLoading(true);
    setManagedError(null);
    setManagedData(null);

    getDocumentChunks(filename)
      .then(setManagedData)
      .catch((e) => setManagedError(e.message))
      .finally(() => setManagedLoading(false));
  }, [filename]);

  useEffect(() => {
    if (!open || !isManagedMode || !filename || isNew) return;
    fetchManagedChunks();
  }, [open, isManagedMode, filename, isNew, fetchManagedChunks]);

  // --- Sync fullContent from fetched store ---
  useEffect(() => {
    if (isManagedMode) return;
    const doc = documents.get(filename);
    if (doc?.fullContent) {
      setFullContent(doc.fullContent);
      setIsLoading(false);
    }
  }, [isManagedMode, documents, filename]);

  // --- Fetch full document content ---
  useEffect(() => {
    if (!open || !filename || isNew) return;

    // In fetched mode, check store first
    if (!isManagedMode) {
      const doc = useFetchedDocumentsStore.getState().documents.get(filename);
      if (doc?.fullContent) {
        setFullContent(doc.fullContent);
        return;
      }
    }

    let cancelled = false;
    setIsLoading(true);
    const fetcher = getContent ?? getDocumentContent;
    fetcher(filename)
      .then((content) => {
        if (!cancelled) {
          setFullContent(content);
          setEditContent(content);
          if (!isManagedMode) {
            markFullDocument(filename, content, "preview");
          }
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
  }, [open, filename, isNew, isManagedMode, markFullDocument, getContent]);

  // --- Scroll to highlighted chunk ---
  useEffect(() => {
    if (!highlightRef.current) return;
    // Delay until layout is settled so scroll measurements are correct.
    requestAnimationFrame(() => {
      highlightRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }, [activeChunkId, managedActiveIndex, fullContent, viewMode]);

  // --- Rechunk handler ---
  const handleRechunk = useCallback(async () => {
    if (!onRechunk) return;
    setIsRechunking(true);
    try {
      await onRechunk();
      fetchManagedChunks();
    } finally {
      setIsRechunking(false);
    }
  }, [onRechunk, fetchManagedChunks]);

  // --- Save handler ---
  const handleSave = async () => {
    if (!onSave || !editFilename.trim()) return;
    setIsSaving(true);
    try {
      await onSave(editFilename.trim(), editContent);
      onOpenChange(false);
    } finally {
      setIsSaving(false);
    }
  };

  if (!isNew && !chunk && !fallbackFilename && !filenameProp) return null;

  // --- Determine sidebar visibility ---
  const hasSidebar = isNew
    ? false
    : isManagedMode
      ? !managedLoading && managedData != null && managedData.chunks.length > 0
      : siblingChunks.length > 0 || true; // always show sidebar in fetched mode for full-doc toggle

  // --- Render helpers ---

  const renderChunkHighlight = (content: string, start: number, end: number) => {
    const before = content.slice(0, start);
    const highlighted = content.slice(start, end);
    const after = content.slice(end);

    return (
      <ScrollArea className="flex-1 min-h-0">
        <pre className="whitespace-pre-wrap text-sm p-4 font-mono">
          <span className="text-muted-foreground">{before}</span>
          <span
            ref={highlightRef}
            className="bg-yellow-200/50 dark:bg-yellow-900/50 border-l-2 border-yellow-500 pl-1"
          >
            {highlighted}
          </span>
          <span className="text-muted-foreground">{after}</span>
        </pre>
      </ScrollArea>
    );
  };

  const renderMainContent = () => {
    if (viewMode === "edit") {
      return (
        <div className="flex flex-1 flex-col min-h-0 overflow-hidden">
          {isNew && (
            <div className="px-4 pt-3">
              <Input
                value={editFilename}
                onChange={(e) => setEditFilename(e.target.value)}
                placeholder="filename.md"
                className="text-lg font-semibold"
              />
            </div>
          )}
          <div className="flex-1 min-h-0 px-4 pt-2 pb-0">
            <Textarea
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              placeholder="Write your markdown content here..."
              className="h-full resize-none font-mono text-sm"
            />
          </div>
          <DialogFooter className="px-4 py-4">
            <Button
              variant="outline"
              onClick={() => {
                if (isNew) {
                  onOpenChange(false);
                } else {
                  setViewMode("full-doc");
                }
              }}
            >
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={isSaving || !editFilename.trim()}>
              {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
            </Button>
          </DialogFooter>
        </div>
      );
    }

    if (isLoading) {
      return (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      );
    }

    // Full-doc markdown view
    if (viewMode === "full-doc") {
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
    if (isManagedMode) {
      // Managed mode: use start_index / end_index
      if (managedActiveIndex != null && managedData && fullContent) {
        const chunkInfo = managedData.chunks[managedActiveIndex];
        if (chunkInfo) {
          return renderChunkHighlight(fullContent, chunkInfo.start_index, chunkInfo.end_index);
        }
      }
      return (
        <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
          Select a chunk from the sidebar
        </div>
      );
    }

    // Fetched mode: find chunk in full content by text
    if (!activeChunk) return null;

    if (fullContent) {
      const chunkText = activeChunk.content;
      const idx = fullContent.indexOf(chunkText);

      if (idx >= 0) {
        return renderChunkHighlight(fullContent, idx, idx + chunkText.length);
      }
    }

    // Fallback: just show chunk content
    return (
      <ScrollArea className="flex-1 min-h-0">
        <pre className="whitespace-pre-wrap text-sm p-4 font-mono">{activeChunk.content}</pre>
      </ScrollArea>
    );
  };

  const renderSidebar = () => {
    if (!hasSidebar) return null;

    if (isManagedMode) {
      // Managed-mode sidebar: chunks from API
      if (managedLoading) {
        return (
          <div className="w-56 shrink-0 border-r flex items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        );
      }
      if (managedError) {
        return (
          <div className="w-56 shrink-0 border-r flex items-center justify-center p-2">
            <p className="text-xs text-muted-foreground">{managedError}</p>
          </div>
        );
      }
      if (!managedData || managedData.chunks.length === 0) return null;

      return (
        <div className="w-56 shrink-0 border-r min-h-0 overflow-y-auto p-2 space-y-1">
          {/* Full Document toggle */}
          <button
            type="button"
            className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors flex items-center gap-1.5 ${
              viewMode === "full-doc" ? "bg-accent text-accent-foreground" : "hover:bg-muted"
            }`}
            onClick={() => {
              setManagedActiveIndex(null);
              setViewMode("full-doc");
            }}
          >
            <FileText className="h-3 w-3 shrink-0" />
            <span className="font-medium">Full document</span>
          </button>

          <div className="border-t my-1" />

          {managedData.chunks.map((chunkInfo, i) => (
            <button
              key={i}
              type="button"
              className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors ${
                viewMode === "chunk" && managedActiveIndex === i
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-muted"
              }`}
              onClick={() => {
                setManagedActiveIndex(i);
                setViewMode("chunk");
              }}
            >
              <div className="flex items-center gap-1.5 flex-wrap">
                <Badge
                  variant={viewMode === "chunk" && managedActiveIndex === i ? "default" : "outline"}
                  className="text-[10px] shrink-0"
                >
                  Chunk #{i}
                </Badge>
                <span className="text-muted-foreground">{chunkInfo.token_count} tokens</span>
              </div>
              <p className="truncate text-muted-foreground mt-0.5">
                {chunkInfo.text.slice(0, 60)}
                {chunkInfo.text.length > 60 ? "..." : ""}
              </p>
            </button>
          ))}
        </div>
      );
    }

    // Fetched-mode sidebar: sibling chunks from store
    return (
      <div className="w-56 shrink-0 border-r min-h-0 overflow-y-auto p-2 space-y-1">
        {/* Full Document toggle */}
        <button
          type="button"
          className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors flex items-center gap-1.5 ${
            viewMode === "full-doc" ? "bg-accent text-accent-foreground" : "hover:bg-muted"
          }`}
          onClick={() => setViewMode("full-doc")}
        >
          <FileText className="h-3 w-3 shrink-0" />
          <span className="font-medium">Full document</span>
        </button>

        {siblingChunks.length > 0 && <div className="border-t my-1" />}

        {/* Chunk entries */}
        {siblingChunks
          .filter((c) => c.position.type !== "full_document")
          .map((sibling) => (
            <button
              key={sibling.id}
              type="button"
              className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors ${
                viewMode === "chunk" && sibling.id === activeChunkId
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-muted"
              }`}
              onClick={() => {
                setActiveChunkId(sibling.id);
                setViewMode("chunk");
              }}
            >
              <div className="flex items-center gap-1.5 flex-wrap">
                <Badge
                  variant={
                    viewMode === "chunk" && sibling.id === activeChunkId ? "default" : "outline"
                  }
                  className="text-[10px] shrink-0"
                >
                  {chunkPositionLabel(sibling.position)}
                </Badge>
                {sibling.score != null && (
                  <span className="text-muted-foreground">{(sibling.score * 100).toFixed(0)}%</span>
                )}
              </div>
              <p className="truncate text-muted-foreground mt-0.5">
                {sibling.content.slice(0, 60)}
                {sibling.content.length > 60 ? "..." : ""}
              </p>
            </button>
          ))}
      </div>
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="h-[85vh] w-[90vw] max-w-5xl! flex flex-col overflow-hidden p-0">
        <DialogHeader className="px-6 pt-6 pb-3 space-y-2">
          <DialogTitle className="truncate pr-8">{filename}</DialogTitle>
          <DialogDescription className="sr-only">
            Document content and chunk context for {filename}
          </DialogDescription>

          {/* Action bar: metadata badges + rechunk + edit */}
          {(showMetadata || editable) && (
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              {showMetadata && managedData && (
                <>
                  <Badge variant="secondary">Chunking: {managedData.pipeline}</Badge>
                  <Badge variant="secondary">Chunk size: {managedData.chunk_size} tokens</Badge>
                  <Badge variant="secondary">{managedData.chunks.length} chunks</Badge>
                  <Badge variant="outline">Created: {formatDate(managedData.created_at)}</Badge>
                </>
              )}
              {onRechunk && viewMode !== "edit" && (
                <Button variant="outline" size="sm" onClick={handleRechunk} disabled={isRechunking}>
                  <RefreshCw className={`h-3 w-3 mr-1 ${isRechunking ? "animate-spin" : ""}`} />
                  Rechunk
                </Button>
              )}
              {editable && viewMode !== "edit" && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setEditContent(fullContent ?? "");
                    setEditFilename(filename);
                    setViewMode("edit");
                  }}
                >
                  <Pencil className="h-3 w-3 mr-1" />
                  Edit
                </Button>
              )}
            </div>
          )}
        </DialogHeader>

        <div className="flex flex-1 min-h-0">
          {renderSidebar()}

          {/* Main content area */}
          <div className="flex-1 flex flex-col min-h-0 min-w-0">
            {/* Chunk info bar (fetched mode only, when viewing a chunk) */}
            {!isManagedMode && viewMode === "chunk" && activeChunk && (
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
