import {
  ExternalLink,
  FileText,
  ImageIcon,
  Pencil,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { formatFileSize, isWebUrl } from "@/lib/utils";
import Markdown from "markdown-to-jsx";
import { startTransition, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  buildAuxLlmConfig,
  deleteAssetDescription,
  generateAssetDescription,
  getDocumentChunks,
  getDocumentContent,
  listDocumentAssets,
  updateAssetDescription,
} from "@/lib/api";
import { MARKDOWN_BASE_OPTIONS, workspaceMarkdownOptions } from "@/components/chat/markdown/config";
import { useSettingsStore } from "@/stores/settings-store";
import { WorkspaceImage } from "./WorkspaceImage";
import {
  type AssetEntry,
  type AssetListResponse,
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
import { Spinner } from "./ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "./ui/tabs";
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
  /** Citation view: hide the sidebar, showing the document with the cited span highlighted. */
  citationView?: boolean;

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
}

type ViewMode = "full-doc" | "chunk" | "edit" | "asset";
type SidebarTab = "chunks" | "assets";

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleString();
}

/**
 * Resolve a chunk's character range within ``fullContent``.
 *
 * Prefers exact character offsets from the backend (semantic-search
 * chunks carry these) so highlights don't round up to whole lines.
 * Falls back to line numbers, then to a text search.
 */
function resolveChunkRange(
  fullContent: string,
  chunk: FetchedChunk,
): { start: number; end: number } | null {
  if (
    chunk.startIndex !== undefined &&
    chunk.endIndex !== undefined &&
    chunk.endIndex > chunk.startIndex &&
    chunk.endIndex <= fullContent.length
  ) {
    return { start: chunk.startIndex, end: chunk.endIndex };
  }

  const position = chunk.position;
  const [startLine, endLine] =
    position.type === "line_range"
      ? [position.startLine, position.endLine]
      : position.type === "line"
        ? [position.line, position.line]
        : [0, 0];

  if (startLine >= 1) {
    const lines = fullContent.split("\n");
    if (startLine > lines.length) return null;
    const e = Math.min(endLine, lines.length);
    let start = 0;
    for (let i = 0; i < startLine - 1; i++) start += lines[i].length + 1;
    let end = start + lines[startLine - 1].length;
    for (let i = startLine; i < e; i++) end += lines[i].length + 1;
    return { start, end };
  }

  if (!chunk.content) return null;
  const idx = fullContent.indexOf(chunk.content);
  return idx >= 0 ? { start: idx, end: idx + chunk.content.length } : null;
}

export function DocumentDialog({
  open,
  onOpenChange,
  filename: filenameProp,
  chunk,
  fallbackFilename,
  initialFullDoc = false,
  citationView = false,
  showMetadata = false,
  onRechunk,
  editable = false,
  isNew = false,
  onSave,
}: DocumentDialogProps) {
  // --- State ---
  // Local content is only used in managed mode (custom getContent fetcher).
  // In fetched mode the store is the source of truth (see `fullContent` below).
  const [localFullContent, setLocalFullContent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("full-doc");
  const [activeChunkId, setActiveChunkId] = useState<string | null>(null);

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

  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("chunks");
  const [assetsData, setAssetsData] = useState<AssetListResponse | null>(null);
  const [assetsLoading, setAssetsLoading] = useState(false);
  const [activeAssetIndex, setActiveAssetIndex] = useState<number | null>(null);
  const [assetDescriptionDraft, setAssetDescriptionDraft] = useState("");
  const [isEditingAssetDescription, setIsEditingAssetDescription] = useState(false);
  const [isSavingAssetDescription, setIsSavingAssetDescription] = useState(false);
  const [isGeneratingAssetDescription, setIsGeneratingAssetDescription] = useState(false);
  const [isDeletingAssetDescription, setIsDeletingAssetDescription] = useState(false);

  // When true, the next scroll-into-view uses "instant" instead of "smooth".
  const isInitialScrollRef = useRef(true);

  // Fetched-mode store access
  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);
  const markFullDocument = useFetchedDocumentsStore((state) => state.markFullDocument);
  const overrides = useSettingsStore((state) => state.overrides);

  const isManagedMode = showMetadata || onRechunk != null;
  const filename = chunk?.filename ?? fallbackFilename ?? filenameProp;

  // Subscribe to just this document's full content so unrelated store
  // updates (e.g., new search chunks streaming in) don't refire the fetch
  // effect below.
  const storedFullContent = useFetchedDocumentsStore((state) =>
    filename ? state.documents.get(filename)?.fullContent : undefined,
  );

  const fullContent = isManagedMode ? localFullContent : (storedFullContent ?? null);

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

  // Stable across renders so the image overrides don't remount (and refetch) every WorkspaceImage.
  const markdownOptions = useMemo(() => workspaceMarkdownOptions(filename), [filename]);

  // --- Reset state on open ---
  useEffect(() => {
    if (!open) return;

    isInitialScrollRef.current = true;
    setEditFilename(filenameProp);
    setManagedData(null);
    setManagedError(null);
    setManagedActiveIndex(null);
    setSidebarTab("chunks");
    setAssetsData(null);
    setActiveAssetIndex(null);
    setIsEditingAssetDescription(false);

    if (isNew) {
      setViewMode("edit");
      setEditContent("");
      setLocalFullContent(null);
    } else if (chunk) {
      setActiveChunkId(chunk.id);
      setViewMode(initialFullDoc || chunk.position.type === "full_document" ? "full-doc" : "chunk");
    } else {
      setActiveChunkId(null);
      setViewMode("full-doc");
    }
  }, [open, chunk, initialFullDoc, isNew, filenameProp]);

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

  const isWeb = isWebUrl(filename);

  // --- Fetch full document content ---
  useEffect(() => {
    if (!open || !filename || isNew) return;

    // In fetched mode the store is the source of truth — skip the fetcher
    // when content is already present (or arrives via a tool output mid-flight).
    if (!isManagedMode && storedFullContent != null) {
      setIsLoading(false);
      return;
    }

    // Don't fetch from backend for web URLs — content comes from the store only
    if (isWebUrl(filename)) {
      setIsLoading(false);
      return;
    }

    let cancelled = false;
    setIsLoading(true);
    getDocumentContent(filename)
      .then((content) => {
        if (cancelled) return;
        if (isManagedMode) {
          setLocalFullContent(content);
        } else {
          markFullDocument(filename, content, "preview");
        }
        setEditContent(content);
        startTransition(() => setIsLoading(false));
      })
      .catch(() => {
        if (cancelled) return;
        if (isManagedMode) setLocalFullContent(null);
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, filename, isNew, isManagedMode, markFullDocument, storedFullContent]);

  // Scroll the highlight into view when its DOM element mounts. The span
  // is keyed by the active chunk identifier (see renderChunkHighlight), so
  // each chunk switch remounts it and re-fires this callback.
  const highlightRef = useCallback((node: HTMLElement | null) => {
    if (!node) return;
    const behavior = isInitialScrollRef.current ? "instant" : "smooth";
    isInitialScrollRef.current = false;
    // Single rAF to let the Radix ScrollArea viewport settle.
    requestAnimationFrame(() => {
      // Center chunks that fit, but anchor the start of chunks taller than
      // the viewport so their beginning stays visible instead of being
      // centered (which would push the start off the top).
      const viewport = node.closest<HTMLElement>('[data-slot="scroll-area-viewport"]');
      const overflows =
        viewport != null && node.getBoundingClientRect().height > viewport.clientHeight;
      node.scrollIntoView({ behavior, block: overflows ? "start" : "center" });
    });
  }, []);
  const highlightKey = isManagedMode ? `m:${managedActiveIndex}` : `f:${activeChunkId}`;

  // --- Fetch assets lazily on first switch to the assets tab ---
  // (404 when the document has none → tab stays hidden via assets_dir)
  useEffect(() => {
    if (!open || !isManagedMode || sidebarTab !== "assets" || assetsData || !filename) return;
    setAssetsLoading(true);
    listDocumentAssets(filename)
      .then(setAssetsData)
      .catch(() => setAssetsData(null))
      .finally(() => setAssetsLoading(false));
  }, [open, isManagedMode, sidebarTab, assetsData, filename]);

  // --- Rechunk handler ---
  const handleRechunk = useCallback(async () => {
    if (!onRechunk) return;
    setIsRechunking(true);
    try {
      await onRechunk();
      fetchManagedChunks();
      // Invalidate the asset list; the lazy effect refetches it on demand.
      setAssetsData(null);
    } finally {
      setIsRechunking(false);
    }
  }, [onRechunk, fetchManagedChunks]);

  const replaceActiveAsset = useCallback(
    (updated: AssetEntry) => {
      setAssetsData((prev) =>
        prev
          ? {
              ...prev,
              assets: prev.assets.map((a, i) => (i === activeAssetIndex ? updated : a)),
            }
          : prev,
      );
    },
    [activeAssetIndex],
  );

  const handleSaveAssetDescription = useCallback(async () => {
    if (activeAssetIndex == null || !assetsData?.assets[activeAssetIndex]) return;
    const asset = assetsData.assets[activeAssetIndex];
    setIsSavingAssetDescription(true);
    try {
      const updated = await updateAssetDescription(filename, asset.name, assetDescriptionDraft);
      replaceActiveAsset(updated);
      setIsEditingAssetDescription(false);
    } finally {
      setIsSavingAssetDescription(false);
    }
  }, [activeAssetIndex, assetsData, assetDescriptionDraft, filename, replaceActiveAsset]);

  const handleGenerateAssetDescription = useCallback(async () => {
    if (activeAssetIndex == null || !assetsData?.assets[activeAssetIndex]) return;
    const asset = assetsData.assets[activeAssetIndex];
    // Image alt-text uses the auxiliary model, matching the import-time describe pipeline.
    const llm = buildAuxLlmConfig(overrides);
    setIsGeneratingAssetDescription(true);
    try {
      const updated = await generateAssetDescription(filename, asset.name, llm);
      replaceActiveAsset(updated);
      setAssetDescriptionDraft(updated.description);
      setIsEditingAssetDescription(false);
    } finally {
      setIsGeneratingAssetDescription(false);
    }
  }, [activeAssetIndex, assetsData, filename, overrides, replaceActiveAsset]);

  const handleDeleteAssetDescription = useCallback(async () => {
    if (activeAssetIndex == null || !assetsData?.assets[activeAssetIndex]) return;
    const asset = assetsData.assets[activeAssetIndex];
    setIsDeletingAssetDescription(true);
    try {
      const updated = await deleteAssetDescription(filename, asset.name);
      replaceActiveAsset(updated);
      setAssetDescriptionDraft("");
      setIsEditingAssetDescription(false);
    } finally {
      setIsDeletingAssetDescription(false);
    }
  }, [activeAssetIndex, assetsData, filename, replaceActiveAsset]);

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
  const hasAssets = Boolean(managedData?.assets_dir);
  const hasSidebar = isNew || citationView
    ? false
    : isManagedMode
      ? managedLoading || (managedData?.chunks.length ?? 0) > 0 || hasAssets
      : true;

  // --- Render helpers ---

  const renderChunkHighlight = (content: string, start: number, end: number) => (
    <ScrollArea className="flex-1 min-h-0">
      <pre className="whitespace-pre-wrap text-sm p-4 font-mono">
        <span className="text-muted-foreground">{content.slice(0, start)}</span>
        <span
          key={highlightKey}
          ref={highlightRef}
          className="bg-yellow-200/50 dark:bg-yellow-900/50 border-l-2 border-yellow-500 pl-1"
        >
          {content.slice(start, end)}
        </span>
        <span className="text-muted-foreground">{content.slice(end)}</span>
      </pre>
    </ScrollArea>
  );

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
                  startTransition(() => setViewMode("full-doc"));
                }
              }}
            >
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={isSaving || !editFilename.trim()}>
              {isSaving ? <Spinner /> : "Save"}
            </Button>
          </DialogFooter>
        </div>
      );
    }

    if (isLoading) {
      return (
        <div className="flex flex-1 items-center justify-center">
          <Spinner className="size-8 text-muted-foreground" />
        </div>
      );
    }

    // Full-doc markdown view
    if (viewMode === "full-doc") {
      if (!fullContent) {
        const isBinary = !filename.toLowerCase().endsWith(".md");
        return (
          <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
            {isBinary ? "Binary file — preview not available" : "Document content unavailable"}
          </div>
        );
      }
      return (
        <ScrollArea className="flex-1 min-h-0">
          <div className="prose prose-sm dark:prose-invert max-w-none p-4">
            <Markdown options={markdownOptions}>{fullContent}</Markdown>
          </div>
        </ScrollArea>
      );
    }

    // Asset view
    if (viewMode === "asset" && activeAssetIndex != null && assetsData?.assets[activeAssetIndex]) {
      const asset = assetsData.assets[activeAssetIndex];
      // WorkspaceImage resolves src relative to the document's directory
      const assetsBasename = assetsData.assets_dir.split("/").pop() ?? "";
      const relativeSrc = `${assetsBasename}/${asset.name}`;
      const assetActionPending = isGeneratingAssetDescription || isDeletingAssetDescription;

      return (
        <ScrollArea className="flex-1 min-h-0">
          <div className="p-4 space-y-4">
            <div className="flex justify-center">
              <WorkspaceImage src={relativeSrc} alt={asset.description} documentPath={filename} />
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Description</h3>
                {editable && !isEditingAssetDescription && (
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={assetActionPending}
                      onClick={handleGenerateAssetDescription}
                    >
                      {isGeneratingAssetDescription ? (
                        <Spinner className="h-3 w-3 mr-1" />
                      ) : (
                        <Sparkles className="h-3 w-3 mr-1" />
                      )}
                      Generate
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={assetActionPending}
                      onClick={() => {
                        setAssetDescriptionDraft(asset.description);
                        setIsEditingAssetDescription(true);
                      }}
                    >
                      <Pencil className="h-3 w-3 mr-1" />
                      Edit
                    </Button>
                    {asset.description && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={assetActionPending}
                        onClick={handleDeleteAssetDescription}
                      >
                        {isDeletingAssetDescription ? (
                          <Spinner className="h-3 w-3 mr-1" />
                        ) : (
                          <Trash2 className="h-3 w-3 mr-1" />
                        )}
                        Delete
                      </Button>
                    )}
                  </div>
                )}
              </div>

              {isEditingAssetDescription ? (
                <div className="space-y-2">
                  <Textarea
                    value={assetDescriptionDraft}
                    onChange={(e) => setAssetDescriptionDraft(e.target.value)}
                    className="text-sm font-mono"
                    rows={6}
                  />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setIsEditingAssetDescription(false)}
                    >
                      Cancel
                    </Button>
                    <Button
                      size="sm"
                      disabled={isSavingAssetDescription}
                      onClick={handleSaveAssetDescription}
                    >
                      {isSavingAssetDescription ? <Spinner /> : "Save"}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="prose prose-sm dark:prose-invert max-w-none">
                  {asset.description ? (
                    <Markdown options={MARKDOWN_BASE_OPTIONS}>{asset.description}</Markdown>
                  ) : (
                    <p className="text-muted-foreground italic">No description</p>
                  )}
                </div>
              )}

              <div className="flex flex-wrap gap-2 pt-2">
                <Badge variant="outline" className="text-xs">
                  {asset.name}
                </Badge>
                {asset.media_type && (
                  <Badge variant="outline" className="text-xs">
                    {asset.media_type}
                  </Badge>
                )}
                <Badge variant="outline" className="text-xs">
                  {formatFileSize(asset.size_bytes)}
                </Badge>
              </div>
            </div>
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

    // Fetched mode: locate chunk in full content using its stored position
    if (!activeChunk) return null;

    if (!fullContent) {
      return (
        <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
          Document content unavailable
        </div>
      );
    }

    const range = resolveChunkRange(fullContent, activeChunk);
    if (range) {
      return renderChunkHighlight(fullContent, range.start, range.end);
    }
    // Couldn't locate the cited line — show the whole document so the
    // user at least has the source in front of them.
    return (
      <ScrollArea className="flex-1 min-h-0">
        <pre className="whitespace-pre-wrap text-sm p-4 font-mono">{fullContent}</pre>
      </ScrollArea>
    );
  };

  const renderSidebar = () => {
    if (!hasSidebar) return null;

    if (isManagedMode) {
      // Managed-mode sidebar: chunks from API + optional assets tab
      if (managedLoading) {
        return (
          <div className="w-56 shrink-0 border-r flex items-center justify-center">
            <Spinner className="size-5 text-muted-foreground" />
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
      if (!managedData || (managedData.chunks.length === 0 && !hasAssets)) return null;

      return (
        <div className="w-56 shrink-0 border-r min-h-0 flex flex-col">
          <div className="p-2 space-y-1 shrink-0">
            {/* Full Document toggle */}
            <button
              type="button"
              className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors flex items-center gap-1.5 ${
                viewMode === "full-doc" ? "bg-accent text-accent-foreground" : "hover:bg-muted"
              }`}
              onClick={() => {
                setManagedActiveIndex(null);
                setActiveAssetIndex(null);
                setIsEditingAssetDescription(false);
                startTransition(() => setViewMode("full-doc"));
              }}
            >
              <FileText className="h-3 w-3 shrink-0" />
              <span className="font-medium">Full document</span>
            </button>
          </div>

          {hasAssets && (
            <div className="px-2 shrink-0">
              <Tabs value={sidebarTab} onValueChange={(v) => setSidebarTab(v as SidebarTab)}>
                <TabsList className="w-full">
                  <TabsTrigger value="chunks" className="flex-1 text-xs">
                    Chunks
                  </TabsTrigger>
                  <TabsTrigger value="assets" className="flex-1 text-xs">
                    Assets
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
          )}

          <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1">
            {!hasAssets && managedData.chunks.length > 0 && <div className="border-t my-1" />}

            {sidebarTab === "chunks" &&
              managedData.chunks.map((chunkInfo, i) => (
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
                    setActiveAssetIndex(null);
                    setIsEditingAssetDescription(false);
                    setViewMode("chunk");
                  }}
                >
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <Badge
                      variant={
                        viewMode === "chunk" && managedActiveIndex === i ? "default" : "outline"
                      }
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

            {sidebarTab === "assets" &&
              (assetsLoading ? (
                <div className="flex items-center justify-center py-4">
                  <Spinner className="size-4 text-muted-foreground" />
                </div>
              ) : (
                assetsData?.assets.map((asset, i) => (
                  <button
                    key={asset.path}
                    type="button"
                    className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors ${
                      viewMode === "asset" && activeAssetIndex === i
                        ? "bg-accent text-accent-foreground"
                        : "hover:bg-muted"
                    }`}
                    onClick={() => {
                      setActiveAssetIndex(i);
                      setManagedActiveIndex(null);
                      setIsEditingAssetDescription(false);
                      setViewMode("asset");
                    }}
                  >
                    <div className="flex items-center gap-1.5">
                      <ImageIcon className="h-3 w-3 shrink-0" />
                      <span className="truncate font-medium">{asset.name}</span>
                    </div>
                    <p className="truncate text-muted-foreground mt-0.5">
                      {asset.description
                        ? asset.description.slice(0, 60) +
                          (asset.description.length > 60 ? "..." : "")
                        : "No description"}
                    </p>
                  </button>
                ))
              ))}
          </div>
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
          onClick={() => startTransition(() => setViewMode("full-doc"))}
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
              <Badge
                variant={viewMode === "chunk" && sibling.id === activeChunkId ? "default" : "outline"}
                className="text-[10px]"
              >
                {chunkPositionLabel(sibling.position)}
              </Badge>
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
          <DialogTitle className="truncate pr-8 flex items-center gap-2">
            {filename}
            {isWeb && (
              <Button
                variant="ghost"
                size="icon"
                className="h-5 w-5 shrink-0"
                onClick={() => window.open(filename, "_blank", "noopener,noreferrer")}
                title="Open in browser"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </Button>
            )}
          </DialogTitle>
          <DialogDescription className="sr-only">
            Document content and chunk context for {filename}
          </DialogDescription>

          {/* Action bar: metadata badges + rechunk + edit */}
          {(showMetadata || editable) && (
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              {showMetadata && managedData && (
                <>
                  <Badge variant="secondary">Chunking: {managedData.pipeline}</Badge>
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
              </div>
            )}
            {renderMainContent()}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
