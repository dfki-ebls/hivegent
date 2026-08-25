import {
  Download,
  ExternalLink,
  FileText,
  ImageIcon,
  Pencil,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { downloadBlob } from "@/lib/download";
import { formatFileSize, isWebUrl } from "@/lib/utils";
import { startTransition, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  buildAuxLlmConfig,
  deleteAssetDescription,
  formatTarget,
  generateAssetDescription,
  getDocumentChunks,
  getDocumentContent,
  listDocumentAssets,
  updateAssetDescription,
} from "@/lib/api";
import { useSettingsStore } from "@/stores/settings-store";
import { AssetImage } from "@/components/documents/AssetImage";
import { WorkspaceImage } from "@/components/WorkspaceImage";
import { WorkspaceMarkdown } from "@/components/WorkspaceMarkdown";
import {
  type AssetEntry,
  type AssetListResponse,
  type ChunkedDocumentResponse,
  type FetchedChunk,
  type FetchedImage,
  chunkOriginLabel,
  chunkPositionLabel,
  isLinePosition,
  lineBounds,
  sortChunks,
} from "@/lib/types";
import { chunksForDocument, useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

interface DocumentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filename: string;

  /** Fetched mode: chunk to highlight, sidebar shows sibling chunks from store. */
  chunk?: FetchedChunk | null;
  /** Fallback filename when no chunk is available (opens in full-doc mode). */
  fallbackFilename?: string;
  /** Image to show full-size in the full-document view (context-panel image docs). */
  image?: FetchedImage | null;
  /** When true the dialog opens directly into the full-document markdown view. */
  initialFullDoc?: boolean;
  /** Management mode: show pipeline/chunk_size/created_at badges. */
  showMetadata?: boolean;
  /** Management mode: show rechunk button and trigger rechunk. */
  onRechunk?: () => Promise<void>;
  /** Reconvert from the original binary (only for documents that have one). */
  onReconvert?: () => void;
  /** Download the original binary (only for documents that have one). */
  onDownloadOriginal?: () => void;

  /** Show edit/preview toggle. */
  editable?: boolean;
  /** New document: editable filename, starts in edit mode. */
  isNew?: boolean;
  /** Canonical directory a new document lands in, shown as a location hint. */
  target?: string;
  /** Save handler for edit mode. */
  onSave?: (filename: string, content: string) => Promise<void>;
}

type ViewMode = "full-doc" | "chunk" | "edit" | "asset";
type SidebarTab = "chunks" | "assets";

interface ContentChunk {
  id: string;
  content: string;
  badge: string;
  detail?: string;
  isFullDocument?: boolean;
  range: { start: number; end: number } | null;
  originLabel?: string;
}

interface ContentModel {
  content: string | null;
  chunks: ContentChunk[];
  loading: boolean;
  error: string | null;
  assetsDir: string | null;
  image: { path: string; mime: string | null } | null;
  metadata: ChunkedDocumentResponse | null;
}

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleString();
}

/** Read-only markdown description with an empty-state fallback. */
function DescriptionBody({ markdown }: { markdown: string | null }) {
  return markdown ? (
    <WorkspaceMarkdown>{markdown}</WorkspaceMarkdown>
  ) : (
    <p className="text-muted-foreground italic text-sm">No description</p>
  );
}

/** Outline badges describing a file's name, media type, and size. */
function FileMetaBadges({
  name,
  mediaType,
  sizeBytes,
}: {
  name: string;
  mediaType?: string | null;
  sizeBytes?: number | null;
}) {
  return (
    <div className="flex flex-wrap gap-2 pt-2">
      <Badge variant="outline" className="text-xs">
        {name}
      </Badge>
      {mediaType && (
        <Badge variant="outline" className="text-xs">
          {mediaType}
        </Badge>
      )}
      {sizeBytes != null && (
        <Badge variant="outline" className="text-xs">
          {formatFileSize(sizeBytes)}
        </Badge>
      )}
    </div>
  );
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
  const [startLine, endLine] = isLinePosition(position) ? lineBounds(position) : [0, 0];

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

function fetchedContentChunk(chunk: FetchedChunk, content: string | null): ContentChunk {
  return {
    id: chunk.id,
    content: chunk.content,
    badge: chunkPositionLabel(chunk.position),
    isFullDocument: chunk.position.type === "full_document",
    range: content ? resolveChunkRange(content, chunk) : null,
    originLabel: chunkOriginLabel(chunk),
  };
}

export function DocumentDialog({
  open,
  onOpenChange,
  filename: filenameProp,
  chunk,
  fallbackFilename,
  image,
  initialFullDoc = false,
  showMetadata = false,
  onRechunk,
  onReconvert,
  onDownloadOriginal,
  editable = false,
  isNew = false,
  target,
  onSave,
}: DocumentDialogProps) {
  // Local content is only used in managed mode (custom getContent fetcher).
  // In fetched mode the store is the source of truth (see `fullContent` below).
  const [localFullContent, setLocalFullContent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("full-doc");
  const [activeChunkId, setActiveChunkId] = useState<string | null>(null);

  const [managedData, setManagedData] = useState<ChunkedDocumentResponse | null>(null);
  const [managedLoading, setManagedLoading] = useState(false);
  const [managedError, setManagedError] = useState<string | null>(null);
  const [isRechunking, setIsRechunking] = useState(false);

  const [editFilename, setEditFilename] = useState(filenameProp);
  const [editContent, setEditContent] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

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

  const contentModel = useMemo<ContentModel>(() => {
    if (isManagedMode) {
      return {
        content: localFullContent,
        chunks:
          managedData?.chunks.map((item, index) => ({
            id: `managed:${index}`,
            content: item.text,
            badge: `Chunk #${index}`,
            detail: `${item.token_count} tokens`,
            range: { start: item.start_index, end: item.end_index },
          })) ?? [],
        loading: isLoading || managedLoading,
        error: managedError,
        assetsDir: managedData?.assets_dir ?? null,
        image:
          managedData?.entry_kind === "image" && managedData.original_path
            ? { path: managedData.original_path, mime: managedData.mime ?? null }
            : null,
        metadata: managedData,
      };
    }

    const content = storedFullContent ?? null;
    const document = filename ? documents.get(filename) : undefined;
    const fetched = document ? chunksForDocument(document, chunks) : [];
    if (chunk && !fetched.some((item) => item.id === chunk.id)) fetched.push(chunk);

    return {
      content,
      chunks: sortChunks(fetched).map((item) => fetchedContentChunk(item, content)),
      loading: isLoading,
      error: null,
      assetsDir: null,
      image: null,
      metadata: null,
    };
  }, [
    chunk,
    isLoading,
    isManagedMode,
    localFullContent,
    managedData,
    managedError,
    managedLoading,
    chunks,
    documents,
    filename,
    storedFullContent,
  ]);

  const fullContent = contentModel.content;
  const activeChunk = contentModel.chunks.find((item) => item.id === activeChunkId) ?? null;

  useEffect(() => {
    if (!open) return;

    isInitialScrollRef.current = true;
    setEditFilename(filenameProp);
    setSaveError(null);
    setManagedData(null);
    setManagedError(null);
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
  // (404 when the document has none → tab stays hidden via assets_dir)
  useEffect(() => {
    if (!open || !isManagedMode || sidebarTab !== "assets" || assetsData || !filename) return;
    setAssetsLoading(true);
    listDocumentAssets(filename)
      .then(setAssetsData)
      .catch(() => setAssetsData(null))
      .finally(() => setAssetsLoading(false));
  }, [open, isManagedMode, sidebarTab, assetsData, filename]);

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

  // Download the markdown description shown in the dialog under its on-disk name.
  const handleDownloadMarkdown = useCallback(() => {
    if (fullContent == null) return;
    const name = filename.slice(filename.lastIndexOf("/") + 1) || "document.md";
    downloadBlob(new Blob([fullContent], { type: "text/markdown" }), name);
  }, [fullContent, filename]);

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

  const handleSave = async () => {
    if (!onSave || !editFilename.trim()) return;
    setIsSaving(true);
    setSaveError(null);
    try {
      await onSave(editFilename.trim(), editContent);
      onOpenChange(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setIsSaving(false);
    }
  };

  if (!isNew && !chunk && !fallbackFilename && !filenameProp) return null;

  // An uploaded image is a single image whose whole caption is one chunk, so we
  // render it like an extracted asset (image + caption + file metadata) and drop
  // the chunk/asset sidebar entirely.
  const imageEntry = contentModel.image;
  const hasAssets = contentModel.assetsDir != null;
  const hasSidebar =
    isNew || imageEntry
      ? false
      : isManagedMode
        ? contentModel.loading || contentModel.chunks.length > 0 || hasAssets
        : true;

  const renderChunkHighlight = (content: string, start: number, end: number) => (
    <ScrollArea className="flex-1 min-h-0">
      <pre className="whitespace-pre-wrap text-sm p-4 font-mono">
        <span className="text-muted-foreground">{content.slice(0, start)}</span>
        <span
          key={activeChunkId}
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
            <div className="px-4 pt-3 space-y-1.5">
              <Input
                value={editFilename}
                onChange={(e) => setEditFilename(e.target.value)}
                placeholder="filename.md"
                className="text-lg font-semibold"
              />
              {target && (
                <p className="text-sm text-muted-foreground">Creating in {formatTarget(target)}</p>
              )}
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
            {saveError && (
              <p className="mr-auto self-center text-sm text-destructive">{saveError}</p>
            )}
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
      // Uploaded image entry: mirror the extracted-asset view — the image
      // itself, its generated caption, and file metadata badges.
      if (imageEntry) {
        const name = imageEntry.path.split("/").pop() ?? filename;
        return (
          <ScrollArea className="flex-1 min-h-0">
            <div className="p-4 space-y-4">
              <div className="flex justify-center">
                <AssetImage
                  filePath={imageEntry.path}
                  alt={filename}
                  wrapperClassName="min-h-32 rounded-md border"
                  className="max-h-[70vh] w-auto max-w-full object-contain"
                />
              </div>
              <div className="space-y-2">
                <h3 className="text-sm font-medium">Description</h3>
                <DescriptionBody markdown={fullContent} />
                <FileMetaBadges name={name} mediaType={imageEntry.mime} />
              </div>
            </div>
          </ScrollArea>
        );
      }
      // Image docs: show the full-size image, with its description below if read.
      if (image) {
        return (
          <ScrollArea className="flex-1 min-h-0">
            <div className="space-y-4 p-4">
              <div className="flex justify-center">
                <AssetImage
                  filePath={image.filePath}
                  alt={filename}
                  wrapperClassName="min-h-32 rounded-md border"
                  className="max-h-[70vh] w-auto max-w-full object-contain"
                />
              </div>
              {fullContent && (
                <WorkspaceMarkdown documentPath={filename}>{fullContent}</WorkspaceMarkdown>
              )}
            </div>
          </ScrollArea>
        );
      }
      if (!fullContent) {
        // No full content fetched — show the excerpts the model actually
        // read (search snippets, grep hits) instead of a dead end.
        if (contentModel.chunks.length > 0) {
          return (
            <ScrollArea className="flex-1 min-h-0">
              <div className="p-4 space-y-4">
                {contentModel.chunks.map((item) => (
                  <div key={item.id} className="space-y-1">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-xs">
                        {item.originLabel}
                      </Badge>
                      <Badge variant="secondary" className="text-xs">
                        {item.badge}
                      </Badge>
                    </div>
                    <pre className="whitespace-pre-wrap text-sm font-mono">{item.content}</pre>
                  </div>
                ))}
              </div>
            </ScrollArea>
          );
        }
        return (
          <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
            {isWeb
              ? "Page content not available — it has not been fetched in this session"
              : !filename.toLowerCase().endsWith(".md")
                ? "Binary file — preview not available"
                : "Document content unavailable"}
          </div>
        );
      }
      return (
        <ScrollArea className="flex-1 min-h-0">
          <WorkspaceMarkdown className="p-4" documentPath={filename}>
            {fullContent}
          </WorkspaceMarkdown>
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
                <DescriptionBody markdown={asset.description} />
              )}

              <FileMetaBadges
                name={asset.name}
                mediaType={asset.media_type}
                sizeBytes={asset.size_bytes}
              />
            </div>
          </div>
        </ScrollArea>
      );
    }

    if (!activeChunk) {
      return (
        <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
          Select a chunk from the sidebar
        </div>
      );
    }

    if (!fullContent) {
      if (activeChunk.content) {
        return (
          <ScrollArea className="flex-1 min-h-0">
            <pre className="whitespace-pre-wrap text-sm p-4 font-mono">{activeChunk.content}</pre>
          </ScrollArea>
        );
      }
      return (
        <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
          Document content unavailable
        </div>
      );
    }

    if (activeChunk.range) {
      return renderChunkHighlight(fullContent, activeChunk.range.start, activeChunk.range.end);
    }

    return (
      <ScrollArea className="flex-1 min-h-0">
        <pre className="whitespace-pre-wrap text-sm p-4 font-mono">{fullContent}</pre>
      </ScrollArea>
    );
  };

  const renderSidebar = () => {
    if (!hasSidebar) return null;

    if (isManagedMode && managedLoading) {
      return (
        <div className="w-56 shrink-0 border-r flex items-center justify-center">
          <Spinner className="size-5 text-muted-foreground" />
        </div>
      );
    }

    if (contentModel.error) {
      return (
        <div className="w-56 shrink-0 border-r flex items-center justify-center p-2">
          <p className="text-xs text-muted-foreground">{contentModel.error}</p>
        </div>
      );
    }

    if (isManagedMode && !contentModel.metadata && !hasAssets) return null;

    return (
      <div className="w-56 shrink-0 border-r min-h-0 flex flex-col">
        <div className="p-2 space-y-1 shrink-0">
          <button
            type="button"
            className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors flex items-center gap-1.5 ${
              viewMode === "full-doc" ? "bg-accent text-accent-foreground" : "hover:bg-muted"
            }`}
            onClick={() => {
              setActiveChunkId(null);
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
            <Tabs value={sidebarTab} onValueChange={(value) => setSidebarTab(value as SidebarTab)}>
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
          {!hasAssets && contentModel.chunks.length > 0 && <div className="border-t my-1" />}

          {sidebarTab === "chunks" &&
            contentModel.chunks
              .filter((item) => !item.isFullDocument)
              .map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors ${
                    viewMode === "chunk" && item.id === activeChunkId
                      ? "bg-accent text-accent-foreground"
                      : "hover:bg-muted"
                  }`}
                  onClick={() => {
                    setActiveChunkId(item.id);
                    setActiveAssetIndex(null);
                    setIsEditingAssetDescription(false);
                    setViewMode("chunk");
                  }}
                >
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <Badge
                      variant={
                        viewMode === "chunk" && item.id === activeChunkId ? "default" : "outline"
                      }
                      className="text-[10px] shrink-0"
                    >
                      {item.badge}
                    </Badge>
                    {item.detail && <span className="text-muted-foreground">{item.detail}</span>}
                  </div>
                  <p className="truncate text-muted-foreground mt-0.5">
                    {item.content.slice(0, 60)}
                    {item.content.length > 60 ? "..." : ""}
                  </p>
                </button>
              ))}

          {sidebarTab === "assets" &&
            (assetsLoading ? (
              <div className="flex items-center justify-center py-4">
                <Spinner className="size-4 text-muted-foreground" />
              </div>
            ) : (
              assetsData?.assets.map((asset, index) => (
                <button
                  key={asset.path}
                  type="button"
                  className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors ${
                    viewMode === "asset" && activeAssetIndex === index
                      ? "bg-accent text-accent-foreground"
                      : "hover:bg-muted"
                  }`}
                  onClick={() => {
                    setActiveAssetIndex(index);
                    setActiveChunkId(null);
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
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="h-[85vh] w-[90vw] max-w-5xl! flex flex-col overflow-hidden p-0">
        <DialogHeader className="px-6 pt-4 pb-1 space-y-1.5">
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

          {/* Metadata badges, then the document actions on their own line below. */}
          {(showMetadata || editable) && (
            <div className="flex flex-col gap-1.5 text-sm text-muted-foreground">
              {showMetadata && contentModel.metadata && (
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">Chunking: {contentModel.metadata.pipeline}</Badge>
                  <Badge variant="secondary">{contentModel.chunks.length} chunks</Badge>
                  {contentModel.metadata.size_bytes != null && (
                    <Badge variant="outline">
                      {formatFileSize(contentModel.metadata.size_bytes)}
                    </Badge>
                  )}
                  <Badge variant="outline">
                    Created: {formatDate(contentModel.metadata.created_at)}
                  </Badge>
                </div>
              )}
              {viewMode !== "edit" && (
                <div className="flex flex-wrap items-center gap-2">
                  {editable && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setEditContent(fullContent ?? "");
                        setEditFilename(filename);
                        setSaveError(null);
                        setViewMode("edit");
                      }}
                    >
                      <Pencil className="h-3 w-3 mr-1" />
                      Edit
                    </Button>
                  )}
                  {onRechunk && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleRechunk}
                      disabled={isRechunking}
                    >
                      <RefreshCw className={`h-3 w-3 mr-1 ${isRechunking ? "animate-spin" : ""}`} />
                      Rechunk
                    </Button>
                  )}
                  {onReconvert && (
                    <Button variant="outline" size="sm" onClick={onReconvert}>
                      <RotateCcw className="h-3 w-3 mr-1" />
                      Reconvert
                    </Button>
                  )}
                  {onDownloadOriginal && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={onDownloadOriginal}
                      title="Download the original file"
                    >
                      <Download className="h-3 w-3 mr-1" />
                      Original
                    </Button>
                  )}
                  {isManagedMode && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleDownloadMarkdown}
                      disabled={fullContent == null}
                      title="Download the markdown description"
                    >
                      <Download className="h-3 w-3 mr-1" />
                      Markdown
                    </Button>
                  )}
                </div>
              )}
            </div>
          )}
        </DialogHeader>

        <div className="flex flex-1 min-h-0">
          {renderSidebar()}

          <div className="flex-1 flex flex-col min-h-0 min-w-0">
            {viewMode === "chunk" && activeChunk?.originLabel && (
              <div className="flex items-center gap-2 px-4 py-2 border-b">
                <Badge variant="outline" className="text-xs">
                  {activeChunk.originLabel}
                </Badge>
                <Badge variant="secondary" className="text-xs">
                  {activeChunk.badge}
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
