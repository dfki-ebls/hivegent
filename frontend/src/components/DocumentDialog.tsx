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
import { useMutation, useQuery } from "@tanstack/react-query";
import { type ReactNode, startTransition, useCallback, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useConversationId } from "@/hooks/chat/use-conversation-id";
import { formatDateTime, formatFileSize } from "@/i18n/format";
import { downloadBlob } from "@/lib/download";
import { basename, centerInScroller, cn, errorMessage, isWebUrl } from "@/lib/utils";
import { buildAuxLlmConfig, formatTarget } from "@/lib/api";
import { documentMutations, documentQueries } from "@/queries/documents";
import { useSettingsStore } from "@/stores/settings-store";
import { AssetImage } from "@/components/documents/AssetImage";
import { EmptyState, LoadingState } from "@/components/EmptyState";
import { ItemButton } from "@/components/RowButtons";
import { ScrollDialogContent } from "@/components/ScrollDialogContent";
import { TooltipButton } from "@/components/TooltipButton";
import { WorkspaceImage } from "@/components/WorkspaceImage";
import { WorkspaceMarkdown } from "@/components/WorkspaceMarkdown";
import {
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
import { CodeBlock, CodeBlockContent, CodeBlockHeader } from "@/components/ui/code-block";
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/components/ui/item";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

interface DocumentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filename: string;

  /** Fetched mode: chunk of *filename* to highlight, sidebar shows sibling chunks from store. */
  chunk?: FetchedChunk | null;
  /** Image to show full-size in the full-document view (context-panel image docs). */
  image?: FetchedImage | null;
  /** When true the dialog opens directly into the full-document markdown view. */
  initialFullDoc?: boolean;
  /** Management mode: show pipeline/chunk_size/created_at badges. */
  showMetadata?: boolean;
  /** Management mode: show rechunk button and trigger rechunk. */
  onRechunk?: () => void;
  /** Whether the rechunk triggered from this dialog is still running. */
  rechunking?: boolean;
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

/** What the main pane shows. */
type View =
  | { mode: "full-doc" }
  | { mode: "chunk"; id: string }
  | { mode: "asset"; index: number }
  | { mode: "edit" };
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

/** Read-only markdown description with an empty-state fallback. */
function DescriptionBody({ markdown }: { markdown: string | null }) {
  const { t } = useTranslation();

  return markdown ? (
    <WorkspaceMarkdown>{markdown}</WorkspaceMarkdown>
  ) : (
    <p className="text-sm text-muted-foreground italic">
      {t(($) => $.documents.dialog.noDescription)}
    </p>
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
    <div className="flex flex-wrap gap-2">
      <Badge variant="outline">{name}</Badge>
      {mediaType && <Badge variant="outline">{mediaType}</Badge>}
      {sizeBytes != null && <Badge variant="outline">{formatFileSize(sizeBytes)}</Badge>}
    </div>
  );
}

/** The origin and position badges of a fetched chunk, where a long search query wraps. */
function ChunkBadges({ chunk }: { chunk: ContentChunk }) {
  return (
    <>
      <Badge variant="outline" className="h-auto justify-start whitespace-normal wrap-anywhere">
        {chunk.originLabel}
      </Badge>
      <Badge variant="secondary">{chunk.badge}</Badge>
    </>
  );
}

/** A scrolling pane with a centered image above its description. */
function ImagePane({ image, children }: { image: ReactNode; children?: ReactNode }) {
  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="flex flex-col gap-4 p-4">
        <div className="flex justify-center">{image}</div>
        {children}
      </div>
    </ScrollArea>
  );
}

/** The document text as one wrapped, scrolling code block filling the pane. */
function TextPane({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col p-4">
      <CodeBlock className="min-h-0 flex-1">
        <CodeBlockContent wrap>
          <span>{children}</span>
        </CodeBlockContent>
      </CodeBlock>
    </div>
  );
}

/** A selectable sidebar entry, highlighted while its view is shown. */
function SidebarItem({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <ItemButton
      size="xs"
      aria-current={active}
      onClick={onClick}
      className={cn(active && "bg-muted")}
    >
      {children}
    </ItemButton>
  );
}

/** Image views (an uploaded image, an extracted asset) title their description alike. */
function DescriptionHeading({ actions }: { actions?: ReactNode }) {
  const { t } = useTranslation();

  return (
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-sm font-medium">{t(($) => $.documents.dialog.descriptionHeading)}</h3>
      {actions}
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

export function DocumentDialog({ open, onOpenChange, ...props }: DocumentDialogProps) {
  // The body mounts with the dialog content, so every opening (and every new
  // target) starts from fresh state instead of resetting it in an effect.
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <ScrollDialogContent className="h-[90dvh] gap-0 overflow-hidden p-0 sm:max-w-5xl">
        <DocumentDialogBody
          key={props.chunk?.id ?? props.filename}
          onOpenChange={onOpenChange}
          {...props}
        />
      </ScrollDialogContent>
    </Dialog>
  );
}

function DocumentDialogBody({
  onOpenChange,
  filename,
  chunk,
  image,
  initialFullDoc = false,
  showMetadata = false,
  onRechunk,
  rechunking = false,
  onReconvert,
  onDownloadOriginal,
  editable = false,
  isNew = false,
  target,
  onSave,
}: Omit<DocumentDialogProps, "open">) {
  const { t } = useTranslation();
  const conversationId = useConversationId();
  const [view, setView] = useState<View>(() => {
    if (isNew) return { mode: "edit" };
    if (!chunk || initialFullDoc || chunk.position.type === "full_document") {
      return { mode: "full-doc" };
    }
    return { mode: "chunk", id: chunk.id };
  });

  const [editFilename, setEditFilename] = useState(filename);
  const [editContent, setEditContent] = useState("");

  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("chunks");
  const [assetDescriptionDraft, setAssetDescriptionDraft] = useState("");
  const [isEditingAssetDescription, setIsEditingAssetDescription] = useState(false);

  // When true, the next scroll-into-view uses "instant" instead of "smooth".
  const isInitialScrollRef = useRef(true);

  // Fetched-mode store access
  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);
  const overrides = useSettingsStore((state) => state.overrides);

  const isManagedMode = showMetadata || onRechunk != null;

  // Content a tool output already delivered, the only source of a web page.
  const storedFullContent = useFetchedDocumentsStore((state) =>
    filename ? state.documents.get(filename)?.fullContent : undefined,
  );

  const isWeb = isWebUrl(filename);
  const wantsManagedChunks = isManagedMode && !!filename && !isNew;
  const needsContent =
    !!filename && !isNew && !isWeb && (isManagedMode || storedFullContent == null);

  // The workspace queries refetch whenever the workspace changes, so an open
  // dialog follows a reconvert, a rechunk, or an edit by the agent.
  const contentQuery = useQuery({
    ...documentQueries.content(filename, conversationId),
    enabled: needsContent,
  });
  const chunksQuery = useQuery({
    ...documentQueries.chunks(filename),
    enabled: wantsManagedChunks,
  });
  // Loaded on the first switch to the assets tab
  // (404 when the document has none → tab stays hidden via assets_dir)
  const assetsQuery = useQuery({
    ...documentQueries.assets(filename),
    enabled: sidebarTab === "assets",
  });
  const assetsData = assetsQuery.data;
  const managedData = chunksQuery.data ?? null;
  const managedLoading = chunksQuery.isLoading;
  const isLoading = contentQuery.isLoading;

  const contentModel = useMemo<ContentModel>(() => {
    if (isManagedMode) {
      return {
        content: contentQuery.data ?? null,
        chunks:
          managedData?.chunks.map((item, index) => ({
            id: `managed:${index}`,
            content: item.text,
            badge: t(($) => $.documents.dialog.chunkNumber, { index }),
            detail: t(($) => $.documents.dialog.tokens, { count: item.token_count }),
            range: { start: item.start_index, end: item.end_index },
          })) ?? [],
        loading: isLoading || managedLoading,
        error: chunksQuery.error && errorMessage(chunksQuery.error),
        assetsDir: managedData?.assets_dir ?? null,
        image:
          managedData?.entry_kind === "image" && managedData.original_path
            ? { path: managedData.original_path, mime: managedData.mime ?? null }
            : null,
        metadata: managedData,
      };
    }

    const content = storedFullContent ?? contentQuery.data ?? null;
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
    contentQuery.data,
    managedData,
    chunksQuery.error,
    managedLoading,
    chunks,
    documents,
    filename,
    storedFullContent,
    t,
  ]);

  const fullContent = contentModel.content;
  const activeChunk =
    view.mode === "chunk"
      ? (contentModel.chunks.find((item) => item.id === view.id) ?? null)
      : null;
  const activeAsset = view.mode === "asset" ? (assetsData?.assets[view.index] ?? null) : null;

  // Scroll the highlight into view when its DOM element mounts. The span
  // is keyed by the active chunk identifier (see renderChunkHighlight), so
  // each chunk switch remounts it and re-fires this callback.
  const highlightRef = useCallback((node: HTMLElement | null) => {
    if (!node) return;
    const behavior = isInitialScrollRef.current ? "instant" : "smooth";
    isInitialScrollRef.current = false;
    // Single rAF to let the code block settle.
    requestAnimationFrame(() => {
      const viewport = node.closest<HTMLElement>('[data-slot="code-block-content"]');
      if (viewport) centerInScroller(node, viewport, behavior);
    });
  }, []);

  // Download the markdown description shown in the dialog under its on-disk name.
  const handleDownloadMarkdown = useCallback(() => {
    if (fullContent == null) return;
    const name = basename(filename) || "document.md";
    downloadBlob(new Blob([fullContent], { type: "text/markdown" }), name);
  }, [fullContent, filename]);

  // One request on the shown asset's description, after which the list refetches.
  const assetAction = useMutation({
    ...documentMutations.assetDescription(filename),
    onSuccess: () => setIsEditingAssetDescription(false),
  });
  const pendingAssetAction = assetAction.isPending ? assetAction.variables.action : null;

  const save = useMutation({
    mutationFn: async ({ name, content }: { name: string; content: string }) =>
      onSave?.(name, content),
    meta: { inlineError: true },
    onSuccess: () => onOpenChange(false),
  });

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

  // Switch the main pane, leaving any asset description edit behind.
  const showView = (next: View) => {
    setIsEditingAssetDescription(false);
    startTransition(() => setView(next));
  };

  const renderMainContent = () => {
    if (view.mode === "edit") {
      return (
        <>
          <div className="flex min-h-0 flex-1 flex-col gap-2 px-4 pt-2">
            {isNew && (
              <Field>
                <Input
                  value={editFilename}
                  onChange={(e) => setEditFilename(e.target.value)}
                  placeholder={t(($) => $.documents.dialog.filenamePlaceholder)}
                  className="text-lg font-semibold"
                />
                {target && (
                  <FieldDescription>
                    {t(($) => $.documents.dialog.creatingIn, { target: formatTarget(target) })}
                  </FieldDescription>
                )}
              </Field>
            )}
            <Textarea
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              placeholder={t(($) => $.documents.dialog.contentPlaceholder)}
              className="min-h-0 flex-1 font-mono text-sm"
            />
          </div>
          <DialogFooter className="p-4">
            {save.error && (
              <FieldError className="mr-auto self-center">{errorMessage(save.error)}</FieldError>
            )}
            <Button
              variant="outline"
              onClick={() => (isNew ? onOpenChange(false) : showView({ mode: "full-doc" }))}
            >
              {t(($) => $.common.actions.cancel)}
            </Button>
            <Button
              onClick={() => save.mutate({ name: editFilename.trim(), content: editContent })}
              disabled={save.isPending || !editFilename.trim()}
            >
              {save.isPending && <Spinner aria-hidden data-icon="inline-start" />}
              {t(($) => $.common.actions.save)}
            </Button>
          </DialogFooter>
        </>
      );
    }

    if (isLoading) return <LoadingState />;

    // Full-doc markdown view
    if (view.mode === "full-doc") {
      // Uploaded image entry: mirror the extracted-asset view — the image
      // itself, its generated caption, and file metadata badges.
      const imagePath = imageEntry?.path ?? image?.filePath;

      if (imagePath) {
        return (
          <ImagePane
            image={
              <AssetImage
                filePath={imagePath}
                alt={filename}
                wrapperClassName="min-h-32 min-w-32 rounded-xl border"
                className="max-h-[70dvh] w-auto max-w-full object-contain"
              />
            }
          >
            {imageEntry ? (
              <div className="flex flex-col gap-2">
                <DescriptionHeading />
                <DescriptionBody markdown={fullContent} />
                <FileMetaBadges name={basename(imageEntry.path)} mediaType={imageEntry.mime} />
              </div>
            ) : (
              // A fetched image doc shows its description below once read.
              fullContent && (
                <WorkspaceMarkdown documentPath={filename}>{fullContent}</WorkspaceMarkdown>
              )
            )}
          </ImagePane>
        );
      }

      if (!fullContent) {
        // No full content fetched, so show the excerpts the model actually
        // read (search snippets, grep hits) instead of a dead end.
        if (contentModel.chunks.length > 0) {
          return (
            <ScrollArea className="min-h-0 flex-1">
              <div className="flex flex-col gap-4 p-4">
                {contentModel.chunks.map((item) => (
                  <CodeBlock key={item.id} size="sm">
                    <CodeBlockHeader className="flex-wrap">
                      <ChunkBadges chunk={item} />
                    </CodeBlockHeader>
                    <CodeBlockContent wrap>{item.content}</CodeBlockContent>
                  </CodeBlock>
                ))}
              </div>
            </ScrollArea>
          );
        }

        return (
          <EmptyState
            description={
              isWeb
                ? t(($) => $.documents.dialog.pageNotFetched)
                : !filename.toLowerCase().endsWith(".md")
                  ? t(($) => $.documents.dialog.binaryFile)
                  : t(($) => $.documents.dialog.contentUnavailable)
            }
          />
        );
      }

      return (
        <ScrollArea className="min-h-0 flex-1">
          <WorkspaceMarkdown className="p-4" documentPath={filename}>
            {fullContent}
          </WorkspaceMarkdown>
        </ScrollArea>
      );
    }

    // Asset view
    if (activeAsset) {
      // WorkspaceImage resolves src relative to the document's directory
      const relativeSrc = `${basename(assetsData?.assets_dir ?? "")}/${activeAsset.name}`;
      const assetActionPending = pendingAssetAction !== null;

      return (
        <ImagePane
          image={
            <WorkspaceImage
              src={relativeSrc}
              alt={activeAsset.description}
              documentPath={filename}
            />
          }
        >
          <div className="flex flex-col gap-2">
            <DescriptionHeading
              actions={
                editable &&
                !isEditingAssetDescription && (
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="xs"
                      disabled={assetActionPending}
                      // Alt-text uses the auxiliary model, like the import-time describe pipeline.
                      onClick={() =>
                        assetAction.mutate({
                          action: "generate",
                          name: activeAsset.name,
                          llm: buildAuxLlmConfig(overrides),
                        })
                      }
                    >
                      {pendingAssetAction === "generate" ? (
                        <Spinner aria-hidden data-icon="inline-start" />
                      ) : (
                        <Sparkles data-icon="inline-start" />
                      )}
                      {t(($) => $.documents.dialog.generate)}
                    </Button>
                    <Button
                      variant="ghost"
                      size="xs"
                      disabled={assetActionPending}
                      onClick={() => {
                        setAssetDescriptionDraft(activeAsset.description);
                        setIsEditingAssetDescription(true);
                      }}
                    >
                      <Pencil data-icon="inline-start" />
                      {t(($) => $.common.actions.edit)}
                    </Button>
                    {activeAsset.description && (
                      <Button
                        variant="ghost"
                        size="xs"
                        disabled={assetActionPending}
                        onClick={() =>
                          assetAction.mutate({ action: "delete", name: activeAsset.name })
                        }
                      >
                        {pendingAssetAction === "delete" ? (
                          <Spinner aria-hidden data-icon="inline-start" />
                        ) : (
                          <Trash2 data-icon="inline-start" />
                        )}
                        {t(($) => $.common.actions.delete)}
                      </Button>
                    )}
                  </div>
                )
              }
            />

            {isEditingAssetDescription ? (
              <>
                <Textarea
                  aria-label={t(($) => $.documents.dialog.descriptionHeading)}
                  value={assetDescriptionDraft}
                  onChange={(e) => setAssetDescriptionDraft(e.target.value)}
                  className="min-h-36 font-mono text-sm"
                />
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setIsEditingAssetDescription(false)}
                  >
                    {t(($) => $.common.actions.cancel)}
                  </Button>
                  <Button
                    size="sm"
                    disabled={assetActionPending}
                    onClick={() =>
                      assetAction.mutate({
                        action: "save",
                        name: activeAsset.name,
                        description: assetDescriptionDraft,
                      })
                    }
                  >
                    {pendingAssetAction === "save" && (
                      <Spinner aria-hidden data-icon="inline-start" />
                    )}
                    {t(($) => $.common.actions.save)}
                  </Button>
                </div>
              </>
            ) : (
              <DescriptionBody markdown={activeAsset.description} />
            )}

            <FileMetaBadges
              name={activeAsset.name}
              mediaType={activeAsset.media_type}
              sizeBytes={activeAsset.size_bytes}
            />
          </div>
        </ImagePane>
      );
    }

    if (!activeChunk) {
      return <EmptyState description={t(($) => $.documents.dialog.selectChunk)} />;
    }

    if (!fullContent) {
      return activeChunk.content ? (
        <TextPane>{activeChunk.content}</TextPane>
      ) : (
        <EmptyState description={t(($) => $.documents.dialog.contentUnavailable)} />
      );
    }

    if (activeChunk.range) {
      const { start, end } = activeChunk.range;

      return (
        <TextPane>
          <span className="text-muted-foreground">{fullContent.slice(0, start)}</span>
          <mark
            key={activeChunk.id}
            ref={highlightRef}
            className="rounded-sm bg-primary/15 box-decoration-clone text-foreground"
          >
            {fullContent.slice(start, end)}
          </mark>
          <span className="text-muted-foreground">{fullContent.slice(end)}</span>
        </TextPane>
      );
    }

    return <TextPane>{fullContent}</TextPane>;
  };

  const renderSidebar = () => {
    if (!hasSidebar) return null;

    if (isManagedMode && managedLoading) {
      return (
        <aside className="flex w-56 shrink-0 border-r">
          <LoadingState />
        </aside>
      );
    }

    if (contentModel.error) {
      return (
        <aside className="flex w-56 shrink-0 border-r">
          <EmptyState description={contentModel.error} />
        </aside>
      );
    }

    if (isManagedMode && !contentModel.metadata && !hasAssets) return null;

    return (
      <aside className="flex min-h-0 w-56 shrink-0 flex-col gap-2 border-r p-2">
        <SidebarItem
          active={view.mode === "full-doc"}
          onClick={() => showView({ mode: "full-doc" })}
        >
          <ItemMedia variant="icon">
            <FileText />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{t(($) => $.options.chunkPosition.fullDocument)}</ItemTitle>
          </ItemContent>
        </SidebarItem>

        {hasAssets ? (
          <Tabs value={sidebarTab} onValueChange={(value) => setSidebarTab(value as SidebarTab)}>
            <TabsList className="w-full">
              <TabsTrigger value="chunks">{t(($) => $.documents.dialog.chunksTab)}</TabsTrigger>
              <TabsTrigger value="assets">{t(($) => $.documents.dialog.assetsTab)}</TabsTrigger>
            </TabsList>
          </Tabs>
        ) : (
          contentModel.chunks.length > 0 && <Separator />
        )}

        <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
          {sidebarTab === "chunks" &&
            contentModel.chunks
              .filter((item) => !item.isFullDocument)
              .map((item) => {
                const active = view.mode === "chunk" && item.id === view.id;

                return (
                  <SidebarItem
                    key={item.id}
                    active={active}
                    onClick={() => showView({ mode: "chunk", id: item.id })}
                  >
                    <ItemContent className="min-w-0">
                      <ItemTitle>
                        <Badge variant={active ? "default" : "outline"}>{item.badge}</Badge>
                        {item.detail && (
                          <span className="text-xs font-normal text-muted-foreground">
                            {item.detail}
                          </span>
                        )}
                      </ItemTitle>
                      <ItemDescription className="line-clamp-1 text-xs">
                        {item.content}
                      </ItemDescription>
                    </ItemContent>
                  </SidebarItem>
                );
              })}

          {sidebarTab === "assets" &&
            (assetsQuery.isLoading ? (
              <Skeleton className="h-12" />
            ) : (
              assetsData?.assets.map((asset, index) => (
                <SidebarItem
                  key={asset.path}
                  active={view.mode === "asset" && view.index === index}
                  onClick={() => showView({ mode: "asset", index })}
                >
                  <ItemMedia variant="icon">
                    <ImageIcon />
                  </ItemMedia>
                  <ItemContent className="min-w-0">
                    <ItemTitle className="max-w-full">{asset.name}</ItemTitle>
                    <ItemDescription className="line-clamp-1 text-xs">
                      {asset.description || t(($) => $.documents.dialog.noDescription)}
                    </ItemDescription>
                  </ItemContent>
                </SidebarItem>
              ))
            ))}
        </div>
      </aside>
    );
  };

  return (
    <>
      <DialogHeader className="px-6 pt-6 pb-4">
        <DialogTitle className="flex min-w-0 items-center gap-2 pr-8 leading-normal">
          <span className="truncate">{filename}</span>
          {isWeb && (
            <TooltipButton
              size="icon-xs"
              label={t(($) => $.documents.dialog.openInBrowser)}
              nativeButton={false}
              // oxlint-disable-next-line jsx-a11y/control-has-associated-label -- the button renders its label into the link
              render={<a href={filename} target="_blank" rel="noreferrer" />}
            >
              <ExternalLink />
            </TooltipButton>
          )}
        </DialogTitle>
        <DialogDescription className="sr-only">
          {t(($) => $.documents.dialog.description, { filename })}
        </DialogDescription>

        {/* Metadata badges, then the document actions on their own line below. */}
        {(showMetadata || editable) && (
          <div className="flex flex-col gap-2 pt-1">
            {showMetadata && contentModel.metadata && (
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">
                  {t(($) => $.documents.dialog.chunking, {
                    pipeline: contentModel.metadata.pipeline,
                  })}
                </Badge>
                <Badge variant="secondary">
                  {t(($) => $.documents.chunks, { count: contentModel.chunks.length })}
                </Badge>
                {contentModel.metadata.size_bytes != null && (
                  <Badge variant="outline">
                    {formatFileSize(contentModel.metadata.size_bytes)}
                  </Badge>
                )}
                <Badge variant="outline">
                  {t(($) => $.documents.dialog.created, {
                    date: formatDateTime(contentModel.metadata.created_at),
                  })}
                </Badge>
              </div>
            )}
            {view.mode !== "edit" && (
              <div className="flex flex-wrap items-center gap-2">
                {editable && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setEditContent(fullContent ?? "");
                      setEditFilename(filename);
                      save.reset();
                      setView({ mode: "edit" });
                    }}
                  >
                    <Pencil data-icon="inline-start" />
                    {t(($) => $.common.actions.edit)}
                  </Button>
                )}
                {onRechunk && (
                  <Button variant="outline" size="sm" onClick={onRechunk} disabled={rechunking}>
                    {rechunking ? (
                      <Spinner aria-hidden data-icon="inline-start" />
                    ) : (
                      <RefreshCw data-icon="inline-start" />
                    )}
                    {t(($) => $.documents.actions.rechunk)}
                  </Button>
                )}
                {onReconvert && (
                  <Button variant="outline" size="sm" onClick={onReconvert}>
                    <RotateCcw data-icon="inline-start" />
                    {t(($) => $.documents.actions.reconvert)}
                  </Button>
                )}
                {onDownloadOriginal && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={onDownloadOriginal}
                    title={t(($) => $.documents.dialog.downloadOriginal)}
                  >
                    <Download data-icon="inline-start" />
                    {t(($) => $.documents.dialog.original)}
                  </Button>
                )}
                {isManagedMode && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleDownloadMarkdown}
                    disabled={fullContent == null}
                    title={t(($) => $.documents.dialog.downloadMarkdown)}
                  >
                    <Download data-icon="inline-start" />
                    {t(($) => $.documents.dialog.markdown)}
                  </Button>
                )}
              </div>
            )}
          </div>
        )}
      </DialogHeader>

      <div className="flex min-h-0 flex-1 border-t">
        {renderSidebar()}

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {activeChunk?.originLabel && (
            <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
              <ChunkBadges chunk={activeChunk} />
            </div>
          )}
          {renderMainContent()}
        </div>
      </div>
    </>
  );
}
