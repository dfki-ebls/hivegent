import { TextSelectIcon } from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { startTransition, useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";

import { canonicalPath, splitScopePath, documentMutations, documentQueries } from "@/api/documents";
import { type FetchedChunk, type FetchedImage } from "@/lib/chunks";
import { isWebUrl } from "@/lib/utils";
import { type DocumentPreview } from "@/lib/original-preview";
import { AssetPane } from "@/components/documents/dialog/asset-pane";
import { EditPane } from "@/components/documents/dialog/edit-pane";
import { DocumentHeader } from "@/components/documents/dialog/header";
import { type SidebarTab, type View } from "@/components/documents/dialog/model";
import { ChunkBadges } from "@/components/documents/dialog/parts";
import { DocumentSidebar } from "@/components/documents/dialog/sidebar";
import { ChunkText, FullDocumentText } from "@/components/documents/dialog/text-views";
import { useDocumentContent } from "@/components/documents/dialog/use-document-content";
import { type PreviewTab, PreviewTabs } from "@/components/documents/preview-tabs";
import { SimilarityView } from "@/components/documents/similarity-view";
import { LoadingPanel, StatusPanel } from "@/components/common/status-panel";
import { ScrollDialogContent } from "@/components/scroll-dialog-content";
import { Dialog } from "@/components/ui/dialog";

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

export function DocumentDialog({ open, onOpenChange, ...props }: DocumentDialogProps) {
  // The body mounts with the dialog content, so every opening (and every new
  // target) starts from fresh state instead of resetting it in an effect.
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <ScrollDialogContent className="h-[90dvh] gap-0 overflow-hidden p-0 sm:max-w-[min(96rem,calc(100%-4rem))]">
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
  const [view, setView] = useState<View>(() => {
    if (isNew) return { mode: "edit" };
    if (!chunk || initialFullDoc || chunk.position.type === "full_document") {
      return { mode: "full-doc" };
    }
    return { mode: "chunk", id: chunk.id };
  });

  // Every view opens on the text, a parsed original loads only once its tab is picked.
  const [tab, setTab] = useState<PreviewTab>("text");
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("chunks");
  const [assetDescriptionDraft, setAssetDescriptionDraft] = useState("");
  const [isEditingAssetDescription, setIsEditingAssetDescription] = useState(false);

  const isManagedMode = showMetadata || onRechunk != null;
  const { contentModel, isLoading } = useDocumentContent({
    filename,
    chunk,
    image,
    isNew,
    isManagedMode,
  });
  // Starts out loading, so the dialog paints its frame before mounting a cached document.
  const loading = useDeferredValue(isLoading, true);
  // Loaded on the first switch to the assets tab
  // (404 when the document has none → tab stays hidden via assets_dir)
  const assetsQuery = useQuery({
    ...documentQueries.assets(filename),
    enabled: sidebarTab === "assets",
  });
  // One request on the shown asset's description, after which the list refetches.
  const assetAction = useMutation({
    ...documentMutations.assetDescription(filename),
    onSuccess: () => setIsEditingAssetDescription(false),
  });

  const isWeb = isWebUrl(filename);
  const assetsData = assetsQuery.data;
  // Both responses name their files relative to the workspace of *filename*.
  const { scope } = splitScopePath(filename);
  const pendingAssetAction = assetAction.isPending ? assetAction.variables.action : null;

  const fullContent = contentModel.content;
  const activeChunk =
    view.mode === "chunk"
      ? (contentModel.chunks.find((item) => item.id === view.id) ?? null)
      : null;
  const activeAsset = view.mode === "asset" ? (assetsData?.assets[view.index] ?? null) : null;

  // An uploaded image or video is shown like an extracted asset (the media, its
  // description, and file metadata), which drops the chunk/asset sidebar entirely.
  const preview = contentModel.preview;
  // A case base original gets the similarity tab, its path canonical in the workspace of *filename*.
  const originalPath = contentModel.metadata?.original_path;
  const caseBasePath =
    originalPath && contentModel.metadata?.case_base_candidate
      ? canonicalPath(scope, originalPath)
      : null;
  const mediaEntry = isManagedMode && preview?.kind === "media" ? preview : null;
  const hasAssets = contentModel.assetsDir != null;
  // The similarity of a case base is about the whole of it, so it takes the sidebar's width too.
  const showsSimilarity = tab === "similarity" && caseBasePath != null && !activeAsset;
  const hasSidebar =
    !(isNew || loading || mediaEntry || showsSimilarity) &&
    (!isManagedMode || contentModel.chunks.length > 0 || hasAssets || contentModel.error != null);

  // Switch the main pane, leaving any asset description edit behind.
  const showView = (next: View) => {
    setIsEditingAssetDescription(false);
    startTransition(() => {
      setView(next);
      setTab("text");
    });
  };

  const renderMainContent = () => {
    if (view.mode === "edit") {
      return (
        <EditPane
          filename={filename}
          initialContent={fullContent ?? ""}
          isNew={isNew}
          target={target}
          onSave={onSave}
          onCancel={() => (isNew ? onOpenChange(false) : showView({ mode: "full-doc" }))}
          onSaved={() => onOpenChange(false)}
        />
      );
    }

    if (loading) return <LoadingPanel />;

    // An extracted asset previews itself, any other view the entry's original.
    const shownPreview: DocumentPreview | null = activeAsset
      ? {
          kind: "media",
          path: canonicalPath(scope, activeAsset.path),
          mediaType: activeAsset.media_type,
          alt: activeAsset.description || activeAsset.name,
        }
      : preview;

    return (
      <PreviewTabs
        preview={shownPreview}
        similarity={caseBasePath && !activeAsset && <SimilarityView path={caseBasePath} />}
        tab={tab}
        onTabChange={setTab}
      >
        {activeChunk?.originLabel && (
          <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
            <ChunkBadges chunk={activeChunk} />
          </div>
        )}
        {renderText()}
      </PreviewTabs>
    );
  };

  const renderText = () => {
    if (view.mode === "full-doc") {
      return (
        <FullDocumentText filename={filename} contentModel={contentModel} mediaEntry={mediaEntry} />
      );
    }

    // Asset view
    if (activeAsset) {
      return (
        <AssetPane
          asset={activeAsset}
          editable={editable}
          editing={isEditingAssetDescription}
          draft={assetDescriptionDraft}
          pendingAction={pendingAssetAction}
          onDraftChange={setAssetDescriptionDraft}
          onEditingChange={setIsEditingAssetDescription}
          onChange={assetAction.mutate}
        />
      );
    }

    if (!activeChunk) {
      return <StatusPanel icon={TextSelectIcon} title={t(($) => $.documents.dialog.selectChunk)} />;
    }

    return <ChunkText chunk={activeChunk} fullContent={fullContent} />;
  };

  return (
    <>
      <DocumentHeader
        filename={filename}
        isWeb={isWeb}
        isManagedMode={isManagedMode}
        showMetadata={showMetadata}
        editable={editable}
        isEditing={view.mode === "edit"}
        contentModel={contentModel}
        rechunking={rechunking}
        onEdit={() => setView({ mode: "edit" })}
        onRechunk={onRechunk}
        onReconvert={onReconvert}
        onDownloadOriginal={onDownloadOriginal}
      />

      <div className="flex min-h-0 flex-1 border-t">
        {hasSidebar && (
          <DocumentSidebar
            view={view}
            onSelect={showView}
            sidebarTab={sidebarTab}
            onSidebarTabChange={setSidebarTab}
            contentModel={contentModel}
            isManagedMode={isManagedMode}
            hasAssets={hasAssets}
            assets={assetsData?.assets}
            assetsLoading={assetsQuery.isLoading}
          />
        )}

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{renderMainContent()}</div>
      </div>
    </>
  );
}
