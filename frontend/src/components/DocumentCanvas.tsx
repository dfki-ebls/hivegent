import {
  AlertCircle,
  Archive,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  FileText,
  FolderOpen,
  FolderPlus,
  Globe,
  Images,
  Loader2,
  Paperclip,
  Plus,
  RotateCcw,
  Scissors,
  Search,
  Trash2,
  Upload,
  Users,
  X,
} from "lucide-react";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  type UploadDocumentOptions,
  buildAuxLlmConfig,
  fetchWorkspaceAsset,
  getGroupDirectories,
  uploadDocument,
} from "../lib/api";
import { DOCUMENT_ACTIONS } from "../lib/document-actions";
import { featureFlags } from "../lib/feature-flags";
import {
  AssetProcessingMode,
  type ChunkingPipeline,
  type ConversionPipeline,
  type DirectoryTreeResponse,
  type DocumentInfo,
  type FetchedChunk,
  type FetchedDocument,
  type FetchedImage,
  type OperationStage,
  type PipelineSpec,
  type UploadProgress,
  chunkPositionLabel,
  sortChunks,
} from "../lib/types";
import {
  buildCollectionZip,
  buildCollectionZipFromDirectoryInput,
  classifyDropItems,
} from "../lib/collection-upload";
import {
  collectFilePaths,
  formatFileSize,
  formatWebUrl,
  isAbortError,
  isWebUrl,
} from "../lib/utils";
import { useObjectUrl } from "@/hooks/use-object-url";
import { useFetchedDocumentsStore } from "../stores/fetched-documents-store";
import { useUserDocumentsStore } from "../stores/user-documents-store";
import { canWriteGroup, getAllGroups, useSettingsStore } from "../stores/settings-store";
import { Checkbox } from "./ui/checkbox";
import { ChunkingPipelineSelector } from "./ChunkingPipelineSelector";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "./ui/collapsible";
import { ConversionPipelineSelector } from "./ConversionPipelineSelector";
import { CreateDirectoryDialog } from "./CreateDirectoryDialog";
import { DirectoryTreeView } from "./DirectoryTreeView";
import { DocumentDialog } from "./DocumentDialog";
import { MoveDocumentDialog } from "./MoveDocumentDialog";
import { Alert, AlertDescription } from "./ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "./ui/alert-dialog";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Progress } from "./ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { ScrollArea } from "./ui/scroll-area";
import { Spinner } from "./ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

// --- Utility functions ---

function fileStem(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

function formatRelativeDate(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
  return date.toLocaleDateString();
}

// --- Shared components ---

interface EmptyStateProps {
  icon: ReactNode;
  title: string;
  description?: string;
}

function EmptyState({ icon, title, description }: EmptyStateProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-muted-foreground">
      {icon}
      <p className="text-center">{title}</p>
      {description && <p className="text-center text-sm">{description}</p>}
    </div>
  );
}

// --- Fetched documents components ---

interface ChunkCardProps {
  chunk: FetchedChunk;
  onClick: () => void;
}

function ChunkCard({ chunk, onClick }: ChunkCardProps) {
  return (
    <button
      type="button"
      className="ml-4 w-[calc(100%-1rem)] rounded-md border bg-card p-3 cursor-pointer transition-colors hover:bg-muted/50 text-left"
      onClick={onClick}
    >
      <div className="flex items-center gap-2 mb-1">
        <Badge variant="outline" className="text-xs">
          {chunk.source}
        </Badge>
        {chunk.score != null && (
          <Badge variant="secondary" className="text-xs">
            {(chunk.score * 100).toFixed(0)}%
          </Badge>
        )}
        <span className="text-xs text-muted-foreground">{chunkPositionLabel(chunk.position)}</span>
      </div>
      <p className="line-clamp-4 text-xs text-muted-foreground">{chunk.content.trim()}</p>
    </button>
  );
}

function ImageThumb({ image }: { image: FetchedImage }) {
  const fetch = useCallback(() => fetchWorkspaceAsset(image.filePath), [image.filePath]);
  const { url, error } = useObjectUrl(fetch);

  if (error) return null;
  return (
    <div className="ml-4 w-[calc(100%-1rem)]">
      {url ? (
        <img
          src={url}
          alt={image.filePath}
          className="max-h-64 w-auto max-w-full rounded-md border"
        />
      ) : (
        <div className="h-32 animate-pulse rounded-md border bg-muted/40" />
      )}
    </div>
  );
}

interface DocumentGroupProps {
  doc: FetchedDocument;
  chunks: FetchedChunk[];
  onChunkClick: (chunk: FetchedChunk) => void;
  onFilenameClick: (filename: string) => void;
}

function DocumentGroup({ doc, chunks, onChunkClick, onFilenameClick }: DocumentGroupProps) {
  const [open, setOpen] = useState(true);
  const isWeb = isWebUrl(doc.filename);
  // Image docs are keyed by their description path; show the image's own name.
  const displayName = doc.image
    ? (doc.image.filePath.split("/").pop() ?? doc.filename)
    : isWeb
      ? formatWebUrl(doc.filename)
      : doc.filename;
  const titlePath = doc.image?.filePath ?? doc.filename;

  // Include all chunks except user-initiated "preview" full-document fetches.
  // Model-fetched full documents appear as regular chunk cards (sorted first).
  const contentChunks = useMemo(() => {
    const visible = chunks.filter(
      (c) => !(c.position.type === "full_document" && c.source === "preview"),
    );
    return sortChunks(visible);
  }, [chunks]);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="flex items-center gap-2 px-1 py-2">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0">
            {isWeb && <Globe className="h-4 w-4" />}
            {!isWeb && (
              <ChevronRight className={`h-4 w-4 transition-transform ${open ? "rotate-90" : ""}`} />
            )}
          </Button>
        </CollapsibleTrigger>
        <button
          type="button"
          className="truncate text-sm font-medium hover:underline text-left min-w-0"
          onClick={() => {
            if (isWeb) {
              window.open(doc.filename, "_blank", "noopener,noreferrer");
            } else {
              onFilenameClick(doc.filename);
            }
          }}
          title={titlePath}
        >
          {displayName}
        </button>
        {contentChunks.length > 0 && (
          <Badge variant="outline" className="shrink-0 text-xs">
            {contentChunks.length} chunk
            {contentChunks.length !== 1 ? "s" : ""}
          </Badge>
        )}
        {doc.bestScore != null && (
          <Badge variant="secondary" className="shrink-0 text-xs">
            {(doc.bestScore * 100).toFixed(0)}%
          </Badge>
        )}
      </div>
      <CollapsibleContent>
        <div className="space-y-2 pb-2">
          {doc.image && <ImageThumb image={doc.image} />}
          {contentChunks.map((chunk) => (
            <ChunkCard key={chunk.id} chunk={chunk} onClick={() => onChunkClick(chunk)} />
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function FetchedDocuments() {
  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);

  // Dialog state
  const [selectedChunk, setSelectedChunk] = useState<FetchedChunk | null>(null);
  const [dialogFilename, setDialogFilename] = useState<string | undefined>(undefined);
  const [initialFullDoc, setInitialFullDoc] = useState(false);
  const dialogOpen = selectedChunk !== null || dialogFilename !== undefined;

  const sortedDocs = useMemo(
    () => Array.from(documents.values()).sort((a, b) => (b.bestScore ?? 0) - (a.bestScore ?? 0)),
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

// --- Manage documents components ---

interface ErrorBannerProps {
  message: string;
  onDismiss: () => void;
}

function ErrorBanner({ message, onDismiss }: ErrorBannerProps) {
  return (
    <Alert variant="destructive" className="m-4 mb-0">
      <AlertCircle className="h-4 w-4" />
      <AlertDescription className="flex items-center justify-between">
        <span>{message}</span>
        <Button variant="ghost" size="sm" className="h-auto p-1" onClick={onDismiss}>
          <X className="h-4 w-4" />
        </Button>
      </AlertDescription>
    </Alert>
  );
}

interface UploadAreaProps {
  isDragging: boolean;
  isUploading: boolean;
  isPreparing: boolean;
  uploadProgress: UploadProgress | null;
  operationStage: OperationStage | null;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  directoryInputRef: React.RefObject<HTMLInputElement | null>;
  zipInputRef: React.RefObject<HTMLInputElement | null>;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onFileInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDirectoryInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onZipInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onSelectFiles: () => void;
  onSelectDirectory: () => void;
  onSelectZip: () => void;
  onNewDocument: () => void;
  onNewFolder: () => void;
  onCancel: () => void;
}

function UploadArea({
  isDragging,
  isUploading,
  isPreparing,
  uploadProgress,
  operationStage,
  fileInputRef,
  directoryInputRef,
  zipInputRef,
  onDragOver,
  onDragLeave,
  onDrop,
  onFileInputChange,
  onDirectoryInputChange,
  onZipInputChange,
  onSelectFiles,
  onSelectDirectory,
  onSelectZip,
  onNewDocument,
  onNewFolder,
  onCancel,
}: UploadAreaProps) {
  const busy = isPreparing || isUploading;
  const busyLabel = isPreparing
    ? "Preparing files..."
    : operationStage
      ? `${operationStage.stage}...`
      : "Uploading...";
  return (
    <div className="border-b p-4">
      <div
        className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-6 transition-colors ${
          isDragging
            ? "border-primary bg-primary/10"
            : "border-muted-foreground/25 bg-muted/25 hover:border-muted-foreground/50 hover:bg-muted/50"
        }`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {busy ? (
          <div className="flex w-full flex-col items-center gap-2">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm font-medium">{busyLabel}</p>
            {uploadProgress && (
              <>
                <p className="max-w-full truncate text-xs text-muted-foreground">
                  {uploadProgress.currentFile}
                </p>
                <div className="flex w-full items-center gap-2">
                  <Progress
                    value={
                      uploadProgress.total > 0
                        ? (uploadProgress.current / uploadProgress.total) * 100
                        : 0
                    }
                    className="flex-1"
                  />
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    {uploadProgress.current} / {uploadProgress.total}
                  </span>
                </div>
                {uploadProgress.failedFiles.length > 0 && (
                  <p className="text-xs text-destructive">
                    {uploadProgress.failedFiles.length} failed
                  </p>
                )}
              </>
            )}
            <Button variant="outline" size="sm" onClick={onCancel}>
              <X className="h-4 w-4 mr-1" />
              Cancel
            </Button>
          </div>
        ) : (
          <>
            <Upload className="h-10 w-10 text-muted-foreground" />
            <div className="text-center">
              <p className="font-medium">Drop files here to upload</p>
              <p className="text-sm text-muted-foreground">or click to browse</p>
            </div>
          </>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          aria-label="Upload files"
          onChange={onFileInputChange}
        />
        <input
          ref={directoryInputRef}
          type="file"
          // @ts-expect-error webkitdirectory is not in React's type definitions
          webkitdirectory=""
          multiple
          className="hidden"
          aria-label="Upload directory"
          onChange={onDirectoryInputChange}
        />
        <input
          ref={zipInputRef}
          type="file"
          accept=".zip"
          className="hidden"
          aria-label="Upload zip archive"
          onChange={onZipInputChange}
        />
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={onSelectFiles} disabled={busy}>
            <Paperclip className="h-4 w-4 mr-1" />
            Select Files
          </Button>
          <Button variant="secondary" size="sm" onClick={onSelectDirectory} disabled={busy}>
            <FolderOpen className="h-4 w-4 mr-1" />
            Upload Folder
          </Button>
          <Button variant="secondary" size="sm" onClick={onSelectZip} disabled={busy}>
            <Archive className="h-4 w-4 mr-1" />
            Upload ZIP
          </Button>
        </div>
        <div className="flex flex-col items-center gap-2 pt-4 border-t border-muted-foreground/15 w-full">
          <p className="text-xs text-muted-foreground">
            Or create and edit documents directly in the browser
          </p>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onNewDocument} disabled={busy}>
              <Plus className="h-4 w-4 mr-1" />
              New Document
            </Button>
            <Button variant="outline" size="sm" onClick={onNewFolder} disabled={busy}>
              <FolderPlus className="h-4 w-4 mr-1" />
              New Folder
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

interface PipelineSettingsBarProps {
  conversionPipeline: ConversionPipeline;
  chunkingPipeline: ChunkingPipeline;
  assetMode: AssetProcessingMode;
  isBulkOperating: boolean;
  onConversionPipelineChange: (pipeline: ConversionPipeline) => void;
  onChunkingPipelineChange: (pipeline: ChunkingPipeline) => void;
  onAssetModeChange: (mode: AssetProcessingMode) => void;
}

function PipelineSettingsBar({
  conversionPipeline,
  chunkingPipeline,
  assetMode,
  isBulkOperating,
  onConversionPipelineChange,
  onChunkingPipelineChange,
  onAssetModeChange,
}: PipelineSettingsBarProps) {
  return (
    <div className="flex items-center justify-center gap-8 border-b px-4 py-3">
      {featureFlags.pipelineSpec && (
        <>
          <ConversionPipelineSelector
            value={conversionPipeline}
            onChange={onConversionPipelineChange}
            disabled={isBulkOperating}
          />
          <ChunkingPipelineSelector
            value={chunkingPipeline}
            onChange={onChunkingPipelineChange}
            disabled={isBulkOperating}
          />
        </>
      )}
      <div className="flex items-center gap-2">
        <Label
          htmlFor="asset-mode-select"
          className="text-sm text-muted-foreground flex items-center gap-1.5"
        >
          <Images className="h-4 w-4" />
          Assets
        </Label>
        <Select
          value={assetMode}
          onValueChange={(v) => onAssetModeChange(v as AssetProcessingMode)}
          disabled={isBulkOperating}
        >
          <SelectTrigger id="asset-mode-select" className="w-[120px]" size="sm">
            <SelectValue placeholder="Select mode" />
          </SelectTrigger>
          <SelectContent>
            {Object.values(AssetProcessingMode).map((mode) => (
              <SelectItem key={mode} value={mode}>
                {mode[0].toUpperCase() + mode.slice(1)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}

interface DocumentListItemProps {
  doc: DocumentInfo;
  isMutating: boolean;
  operationStage: OperationStage | null;
  onEdit: () => void;
  onIncludeDocument: () => void;
  onExcludeDocument: () => void;
  onReconvert: () => void;
  onRemove: () => void;
  selected?: boolean;
  onToggleSelect?: () => void;
}

function DocumentListItem({
  doc,
  isMutating,
  operationStage,
  onEdit,
  onIncludeDocument,
  onExcludeDocument,
  onReconvert,
  onRemove,
  selected,
  onToggleSelect,
}: DocumentListItemProps) {
  return (
    <button
      type="button"
      className="flex w-full items-center gap-3 rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50 cursor-pointer text-left"
      onClick={onEdit}
    >
      {onToggleSelect && (
        <Checkbox
          checked={selected ?? false}
          onCheckedChange={() => onToggleSelect()}
          onClick={(e) => e.stopPropagation()}
          className="shrink-0"
        />
      )}
      <FileText className="h-8 w-8 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate font-medium text-sm">{doc.display_name}</p>
          {isMutating && <Spinner className="size-3 shrink-0 text-muted-foreground" />}
          {isMutating && operationStage && (
            <span className="truncate text-xs text-muted-foreground">
              {operationStage.stage}...
            </span>
          )}
          {doc.chunk_count != null && (
            <Badge variant="outline" className="shrink-0 text-xs gap-1">
              <Scissors className="h-3 w-3" />
              {doc.chunk_count}
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          {formatFileSize(doc.size_bytes)} · {formatRelativeDate(doc.modified_at)}
        </p>
      </div>
      {doc.has_original && (
        <Button
          variant="ghost"
          size="icon"
          title="Reconvert from original"
          onClick={(e) => {
            e.stopPropagation();
            onReconvert();
          }}
          disabled={isMutating}
        >
          <RotateCcw className="h-4 w-4" />
        </Button>
      )}
      <Button
        variant="ghost"
        size="icon"
        title="Include in chat"
        onClick={(e) => {
          e.stopPropagation();
          onIncludeDocument();
        }}
      >
        <Eye className="h-4 w-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        title="Exclude from chat"
        onClick={(e) => {
          e.stopPropagation();
          onExcludeDocument();
        }}
      >
        <EyeOff className="h-4 w-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        title="Remove"
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        disabled={isMutating}
      >
        <Trash2 className="h-4 w-4 text-destructive" />
      </Button>
    </button>
  );
}

// --- Group documents components ---

interface GroupDocumentsSectionProps {
  groupId: string;
  canWrite: boolean;
  onInclude: (path: string) => void;
  onExclude: (path: string) => void;
  onViewFile: (groupId: string, filepath: string) => void;
  onRemoveFile?: (groupId: string, filepath: string) => void;
  onCreateSubdir?: (groupId: string, parentPath: string) => void;
  onDeleteDir?: (groupId: string, dirPath: string) => void;
}

function GroupDocumentsSection({
  groupId,
  canWrite,
  onInclude,
  onExclude,
  onViewFile,
  onRemoveFile,
  onCreateSubdir,
  onDeleteDir,
}: GroupDocumentsSectionProps) {
  const [tree, setTree] = useState<DirectoryTreeResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isOpen, setIsOpen] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);

  useEffect(() => {
    if (!isOpen || hasLoaded) return;
    setIsLoading(true);
    getGroupDirectories(groupId)
      .then(setTree)
      .catch(() => setTree(null))
      .finally(() => {
        setIsLoading(false);
        setHasLoaded(true);
      });
  }, [groupId, isOpen, hasLoaded]);

  const handleInclude = useCallback(
    (path: string) => onInclude(`@${groupId}/${path}`),
    [groupId, onInclude],
  );

  const handleExclude = useCallback(
    (path: string) => onExclude(`@${groupId}/${path}`),
    [groupId, onExclude],
  );

  const handleIncludeGroup = useCallback(() => onInclude(`@${groupId}/`), [groupId, onInclude]);

  const handleExcludeGroup = useCallback(() => onExclude(`@${groupId}/`), [groupId, onExclude]);

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <div className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-x-3 rounded-md px-2 py-1.5 hover:bg-muted/50 group">
        <div className="flex items-center gap-2 min-w-0">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0">
              {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </Button>
          </CollapsibleTrigger>
          <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
          <CollapsibleTrigger asChild>
            <button type="button" className="min-w-0 truncate text-sm font-medium text-left">
              {groupId}
            </button>
          </CollapsibleTrigger>
        </div>
        <div className="flex gap-0.5 opacity-0 group-hover:opacity-100">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Include group in chat"
            onClick={handleIncludeGroup}
          >
            <Eye className="h-3 w-3" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Exclude group from chat"
            onClick={handleExcludeGroup}
          >
            <EyeOff className="h-3 w-3" />
          </Button>
        </div>
        <span />
        <div className="flex justify-end">
          {tree && (
            <Badge variant="secondary" className="text-xs">
              {tree.total_files}
            </Badge>
          )}
        </div>
      </div>
      <CollapsibleContent>
        {isLoading && (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {!isLoading && tree && tree.total_files > 0 && (
          <div className="ml-4">
            <DirectoryTreeView
              entry={tree.root}
              onEditFile={(path) => onViewFile(groupId, path)}
              onInclude={handleInclude}
              onExclude={handleExclude}
              onFileAction={
                canWrite && onRemoveFile
                  ? (path, actionId) => {
                      if (actionId === "delete") onRemoveFile(groupId, path);
                    }
                  : undefined
              }
              onCreateSubdir={
                canWrite && onCreateSubdir ? (path) => onCreateSubdir(groupId, path) : undefined
              }
              onDeleteDir={
                canWrite && onDeleteDir ? (path) => onDeleteDir(groupId, path) : undefined
              }
            />
          </div>
        )}
        {!isLoading && (!tree || tree.total_files === 0) && (
          <p className="ml-8 py-2 text-xs text-muted-foreground">No documents in this group</p>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}

interface DialogState {
  filename: string;
  /** Show metadata badges and chunk sidebar from API. */
  showMetadata: boolean;
  /** Enable editing. */
  editable: boolean;
  /** New document mode. */
  isNew: boolean;
}

interface ManageDocumentsProps {
  onIncludeDocument?: (filename: string) => void;
  onExcludeDocument?: (filename: string) => void;
}

/** Recursively collect all file paths from a directory entry. */
function ManageDocuments({ onIncludeDocument, onExcludeDocument }: ManageDocumentsProps) {
  const {
    documents,
    directoryTree,
    mutatingPaths,
    isUploading,
    hasFetched,
    uploadProgress,
    bulkProgress,
    operationStage,
    error,
    refresh,
    upload,
    uploadMultiple,
    uploadCol,
    remove,
    rechunk: storeRechunk,
    reconvert: storeReconvert,
    bulkRechunk: storeBulkRechunk,
    bulkReconvert: storeBulkReconvert,
    bulkDelete: storeBulkDelete,
    move: storeMove,
    createDir,
    deleteDir,
    moveDir: storeMoveDir,
    clearError,
  } = useUserDocumentsStore();
  const overrides = useSettingsStore((state) => state.overrides);
  const conversionPipeline = useSettingsStore((state) => state.conversionPipeline);
  const chunkingPipeline = useSettingsStore((state) => state.chunkingPipeline);
  const conversionConfigs = useSettingsStore((state) => state.conversionConfigs);
  const chunkingConfigs = useSettingsStore((state) => state.chunkingConfigs);
  const setConversionPipeline = useSettingsStore((state) => state.setConversionPipeline);
  const setChunkingPipeline = useSettingsStore((state) => state.setChunkingPipeline);
  const assetMode = useSettingsStore((state) => state.assetMode);
  const setAssetMode = useSettingsStore((state) => state.setAssetMode);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isPreparing, setIsPreparing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const beginOp = useCallback((): AbortSignal => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    return ctrl.signal;
  }, []);

  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const [dialogState, setDialogState] = useState<DialogState | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filteredDocuments, setFilteredDocuments] = useState(documents);
  const [moveFilePath, setMoveFilePath] = useState<string | null>(null);
  const [moveDirPath, setMoveDirPath] = useState<string | null>(null);
  const [bulkMoveFiles, setBulkMoveFiles] = useState<string[] | null>(null);
  const [createDirParent, setCreateDirParent] = useState<string | undefined>(undefined);
  const [showCreateDir, setShowCreateDir] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [pendingDelete, setPendingDelete] = useState<
    | { kind: "file"; path: string }
    | { kind: "directory"; path: string }
    | { kind: "bulk"; files: string[] }
    | null
  >(null);
  const [pendingOverwrite, setPendingOverwrite] = useState<{
    files: File[];
    options: UploadDocumentOptions;
    conflicting: string[];
  } | null>(null);

  const toggleFile = useCallback((path: string) => {
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelectedFiles(new Set()), []);

  const toggleDirFiles = useCallback((paths: string[]) => {
    setSelectedFiles((prev) => {
      const allSelected = paths.every((p) => prev.has(p));
      const next = new Set(prev);
      for (const p of paths) {
        if (allSelected) {
          next.delete(p);
        } else {
          next.add(p);
        }
      }
      return next;
    });
  }, []);

  const uploadInFlight = isPreparing || isUploading || bulkProgress !== null;
  useEffect(() => {
    if (!uploadInFlight) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [uploadInFlight]);

  useEffect(() => {
    if (!searchQuery.trim()) {
      setFilteredDocuments(documents);
      return;
    }

    let cancelled = false;

    void import("fuse.js").then(({ default: Fuse }) => {
      if (cancelled) return;

      const fuse = new Fuse(documents, {
        keys: ["display_name", "filename"],
        threshold: 0.4,
      });
      setFilteredDocuments(fuse.search(searchQuery).map((result) => result.item));
    });

    return () => {
      cancelled = true;
    };
  }, [documents, searchQuery]);

  const isSearching = searchQuery.trim().length > 0;

  const visibleFilePaths = useMemo(() => {
    if (isSearching) {
      return filteredDocuments.map((d) => d.filename);
    }
    if (directoryTree) {
      return collectFilePaths(directoryTree.root);
    }
    return documents.map((d) => d.filename);
  }, [isSearching, filteredDocuments, directoryTree, documents]);

  const { allSelected, someSelected } = useMemo(() => {
    let count = 0;
    for (const p of visibleFilePaths) {
      if (selectedFiles.has(p)) count++;
    }
    return {
      allSelected: visibleFilePaths.length > 0 && count === visibleFilePaths.length,
      someSelected: count > 0,
    };
  }, [visibleFilePaths, selectedFiles]);

  const toggleSelectAll = useCallback(() => {
    if (allSelected) {
      clearSelection();
    } else {
      setSelectedFiles(new Set(visibleFilePaths));
    }
  }, [allSelected, visibleFilePaths, clearSelection]);

  const docsByFilename = useMemo(() => new Map(documents.map((d) => [d.filename, d])), [documents]);

  const selectedReconvertable = useMemo(
    () => [...selectedFiles].filter((f) => docsByFilename.get(f)?.has_original === true),
    [selectedFiles, docsByFilename],
  );

  const pipelineSpec: PipelineSpec = useMemo(
    () =>
      featureFlags.pipelineSpec
        ? {
            conversion: {
              pipeline: conversionPipeline,
              config: conversionConfigs[conversionPipeline],
            },
            chunking: {
              pipeline: chunkingPipeline,
              config: chunkingConfigs[chunkingPipeline],
            },
            process_assets: assetMode,
          }
        : { process_assets: assetMode },
    [conversionPipeline, chunkingPipeline, conversionConfigs, chunkingConfigs, assetMode],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // --- Dialog handlers ---

  const handleEdit = useCallback((filepath: string) => {
    setDialogState({
      filename: filepath,
      showMetadata: true,
      editable: true,
      isNew: false,
    });
  }, []);

  const handleNew = useCallback(() => {
    setDialogState({
      filename: "new-document.md",
      showMetadata: false,
      editable: true,
      isNew: true,
    });
  }, []);

  const handleViewGroupFile = useCallback((groupId: string, filepath: string) => {
    setDialogState({
      filename: `@${groupId}/${filepath}`,
      showMetadata: false,
      editable: false,
      isNew: false,
    });
  }, []);

  const handleSave = useCallback(
    async (filename: string, content: string) => {
      const file = new File([content], filename, { type: "text/plain" });
      await uploadDocument(filename, file, { spec: pipelineSpec });
      await refresh();
    },
    [refresh, pipelineSpec],
  );

  // --- Include / exclude handlers ---

  const handleInclude = useCallback(
    (path: string) => {
      onIncludeDocument?.(path);
    },
    [onIncludeDocument],
  );

  const handleExclude = useCallback(
    (path: string) => {
      onExcludeDocument?.(path);
    },
    [onExcludeDocument],
  );

  // --- Rechunk / reconvert handlers ---

  const handleReconvert = useCallback(
    async (filepath: string) => {
      await storeReconvert(filepath, {
        spec: pipelineSpec,
        llm: buildAuxLlmConfig(overrides),
      });
    },
    [storeReconvert, pipelineSpec, overrides],
  );

  const handleDownloadOriginal = useCallback(
    async (filepath: string) => {
      try {
        const { downloadOriginal } = await import("../lib/api");
        const blob = await downloadOriginal(filepath);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = docsByFilename.get(filepath)?.original_path?.split("/").pop() ?? "original";
        a.click();
        URL.revokeObjectURL(url);
      } catch {
        // silently ignore download errors
      }
    },
    [docsByFilename],
  );

  // --- Bulk operation handlers ---

  const handleBulkRechunk = useCallback(async () => {
    const files = [...selectedFiles];
    clearSelection();
    await storeBulkRechunk(files, pipelineSpec);
  }, [selectedFiles, clearSelection, storeBulkRechunk, pipelineSpec]);

  const handleBulkReconvert = useCallback(async () => {
    const files = [...selectedReconvertable];
    clearSelection();
    await storeBulkReconvert(files, pipelineSpec, buildAuxLlmConfig(overrides));
  }, [selectedReconvertable, clearSelection, storeBulkReconvert, pipelineSpec, overrides]);

  const handleBulkDelete = useCallback(() => {
    setPendingDelete({ kind: "bulk", files: [...selectedFiles] });
  }, [selectedFiles]);

  const handleBulkDownload = useCallback(async () => {
    for (const filepath of selectedReconvertable) {
      await handleDownloadOriginal(filepath);
    }
  }, [selectedReconvertable, handleDownloadOriginal]);

  const handleBulkMove = useCallback(() => {
    setBulkMoveFiles([...selectedFiles]);
  }, [selectedFiles]);

  const handleBulkMoveConfirm = useCallback(
    async (destinationDir: string) => {
      const files = bulkMoveFiles ?? [];
      setBulkMoveFiles(null);
      clearSelection();
      for (const filepath of files) {
        const filename = filepath.split("/").pop() ?? filepath;
        const destination = destinationDir ? `${destinationDir}/${filename}` : filename;
        await storeMove(filepath, destination);
      }
    },
    [bulkMoveFiles, clearSelection, storeMove],
  );

  const bulkHandlers = useMemo<Record<string, () => void>>(
    () => ({
      rechunk: () => void handleBulkRechunk(),
      reconvert: () => void handleBulkReconvert(),
      download: () => void handleBulkDownload(),
      move: handleBulkMove,
      delete: handleBulkDelete,
    }),
    [handleBulkRechunk, handleBulkReconvert, handleBulkDownload, handleBulkMove, handleBulkDelete],
  );

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setPendingDelete(null);
    switch (pendingDelete.kind) {
      case "file":
        await remove(pendingDelete.path);
        break;
      case "directory":
        await deleteDir(pendingDelete.path);
        break;
      case "bulk":
        clearSelection();
        await storeBulkDelete(pendingDelete.files);
        break;
    }
  }, [pendingDelete, remove, deleteDir, clearSelection, storeBulkDelete]);

  const confirmOverwrite = useCallback(async () => {
    if (!pendingOverwrite) return;
    const { files, options } = pendingOverwrite;
    setPendingOverwrite(null);
    const signal = beginOp();
    const overwriteOptions = { ...options, overwrite: true, signal };
    if (files.length === 1) {
      await upload(files[0], overwriteOptions);
    } else {
      await uploadMultiple(files, overwriteOptions);
    }
  }, [beginOp, pendingOverwrite, upload, uploadMultiple]);

  // --- File upload handlers ---

  const uploadOptions = useMemo<UploadDocumentOptions>(
    () => ({
      spec: pipelineSpec,
      llm: buildAuxLlmConfig(overrides),
    }),
    [pipelineSpec, overrides],
  );

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const fileArray = Array.from(files);

      // Check for duplicate stems within the batch
      const stems = fileArray.map((f) => fileStem(f.name));
      const seen = new Set<string>();
      for (const stem of stems) {
        if (seen.has(stem)) {
          useUserDocumentsStore.setState({
            error: `Batch contains files with the same stem "${stem}"`,
          });
          return;
        }
        seen.add(stem);
      }

      // Check stems against existing documents
      const existingStems = new Set(documents.map((d) => fileStem(d.filename)));
      const conflicting = stems.filter((s) => existingStems.has(s));
      if (conflicting.length > 0) {
        setPendingOverwrite({ files: fileArray, options: uploadOptions, conflicting });
        return;
      }

      const signal = beginOp();
      if (fileArray.length === 1) {
        await upload(fileArray[0], { ...uploadOptions, signal });
      } else {
        await uploadMultiple(fileArray, { ...uploadOptions, signal });
      }
    },
    [beginOp, upload, uploadMultiple, uploadOptions, documents],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);

      const { items, files } = e.dataTransfer;
      if (files.length === 0 && items.length === 0) return;

      const signal = beginOp();
      setIsPreparing(true);
      try {
        const classification = await classifyDropItems(items, files, signal);
        const hasCollection =
          classification.directories.length > 0 || classification.zipFiles.length > 0;

        if (!hasCollection) {
          void handleFiles(files);
          return;
        }

        const collection = await buildCollectionZip(classification, signal);
        setIsPreparing(false);
        await uploadCol(collection, { ...uploadOptions, signal });
      } catch (err) {
        if (!isAbortError(err)) throw err;
      } finally {
        setIsPreparing(false);
      }
    },
    [beginOp, handleFiles, uploadCol, uploadOptions],
  );

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      void handleFiles(e.target.files);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    },
    [handleFiles],
  );

  const handleDirectoryInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;

      const signal = beginOp();
      setIsPreparing(true);
      try {
        const collection = await buildCollectionZipFromDirectoryInput(files);
        setIsPreparing(false);
        await uploadCol(collection, { ...uploadOptions, signal });
      } catch (err) {
        if (!isAbortError(err)) throw err;
      } finally {
        setIsPreparing(false);
        if (directoryInputRef.current) {
          directoryInputRef.current.value = "";
        }
      }
    },
    [beginOp, uploadCol, uploadOptions],
  );

  const handleZipInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;

      const file = files[0];
      if (!file.name.toLowerCase().endsWith(".zip")) return;

      const signal = beginOp();
      try {
        await uploadCol(file, { ...uploadOptions, signal });
      } catch (err) {
        if (!isAbortError(err)) throw err;
      } finally {
        if (zipInputRef.current) {
          zipInputRef.current.value = "";
        }
      }
    },
    [beginOp, uploadCol, uploadOptions],
  );

  // --- Directory handlers ---

  const handleCreateSubdir = useCallback((parentPath: string) => {
    setCreateDirParent(parentPath || undefined);
    setShowCreateDir(true);
  }, []);

  const handleNewFolder = useCallback(() => {
    setCreateDirParent(undefined);
    setShowCreateDir(true);
  }, []);

  // --- Render helpers ---

  const renderFlatList = () => {
    if (filteredDocuments.length === 0) {
      return (
        <EmptyState
          icon={<Search className="h-12 w-12 opacity-50" />}
          title="No matching documents"
        />
      );
    }

    return (
      <div className="space-y-2">
        {filteredDocuments.map((doc) => {
          const docMutating = mutatingPaths.has(doc.filename);
          return (
            <DocumentListItem
              key={doc.filename}
              doc={doc}
              isMutating={docMutating}
              operationStage={docMutating ? operationStage : null}
              onEdit={() => handleEdit(doc.filename)}
              onIncludeDocument={() => handleInclude(doc.filename)}
              onExcludeDocument={() => handleExclude(doc.filename)}
              onReconvert={() => handleReconvert(doc.filename)}
              onRemove={() => setPendingDelete({ kind: "file", path: doc.filename })}
              selected={selectedFiles.has(doc.filename)}
              onToggleSelect={() => toggleFile(doc.filename)}
            />
          );
        })}
      </div>
    );
  };

  const renderTreeView = () => {
    if (!directoryTree) {
      if (documents.length === 0) {
        return (
          <EmptyState
            icon={<FileText className="h-12 w-12 opacity-50" />}
            title="No documents yet"
            description="Upload documents to get started"
          />
        );
      }
      return null;
    }

    if (directoryTree.total_files === 0 && directoryTree.total_directories === 0) {
      return (
        <EmptyState
          icon={<FileText className="h-12 w-12 opacity-50" />}
          title="No documents yet"
          description="Upload documents to get started"
        />
      );
    }

    return (
      <DirectoryTreeView
        entry={directoryTree.root}
        mutatingPaths={mutatingPaths}
        operationStage={operationStage}
        onEditFile={handleEdit}
        onInclude={handleInclude}
        onExclude={handleExclude}
        onFileAction={(path, actionId) => {
          switch (actionId) {
            case "rechunk":
              void storeRechunk(path, pipelineSpec);
              break;
            case "reconvert":
              void handleReconvert(path);
              break;
            case "download":
              void handleDownloadOriginal(path);
              break;
            case "move":
              setMoveFilePath(path);
              break;
            case "delete":
              setPendingDelete({ kind: "file", path });
              break;
          }
        }}
        onCreateSubdir={handleCreateSubdir}
        onDeleteDir={(path) => setPendingDelete({ kind: "directory", path })}
        onMoveDir={(path) => setMoveDirPath(path)}
        selectedFiles={selectedFiles}
        onToggleSelectFile={toggleFile}
        onToggleSelectDir={toggleDirFiles}
      />
    );
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {error && <ErrorBanner message={error} onDismiss={clearError} />}

      <UploadArea
        isDragging={isDragging}
        isUploading={isUploading}
        isPreparing={isPreparing}
        uploadProgress={uploadProgress}
        operationStage={operationStage}
        fileInputRef={fileInputRef}
        directoryInputRef={directoryInputRef}
        zipInputRef={zipInputRef}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onFileInputChange={handleFileInputChange}
        onDirectoryInputChange={handleDirectoryInputChange}
        onZipInputChange={handleZipInputChange}
        onSelectFiles={() => fileInputRef.current?.click()}
        onSelectDirectory={() => directoryInputRef.current?.click()}
        onSelectZip={() => zipInputRef.current?.click()}
        onNewDocument={handleNew}
        onNewFolder={handleNewFolder}
        onCancel={handleCancel}
      />

      <PipelineSettingsBar
        conversionPipeline={conversionPipeline}
        chunkingPipeline={chunkingPipeline}
        assetMode={assetMode}
        isBulkOperating={bulkProgress !== null}
        onConversionPipelineChange={setConversionPipeline}
        onChunkingPipelineChange={setChunkingPipeline}
        onAssetModeChange={setAssetMode}
      />

      <div className="p-4 pb-2">
        {selectedFiles.size > 0 || bulkProgress ? (
          <div className="flex h-9 items-center justify-center gap-2">
            {bulkProgress ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin shrink-0" />
                <span className="truncate text-sm">{bulkProgress.currentFile}</span>
                <Progress
                  value={
                    bulkProgress.total > 0 ? (bulkProgress.current / bulkProgress.total) * 100 : 0
                  }
                  className="w-24 shrink-0"
                />
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                  {bulkProgress.current}/{bulkProgress.total}
                </span>
                {bulkProgress.failedFiles.length > 0 && (
                  <span className="shrink-0 text-xs text-destructive">
                    {bulkProgress.failedFiles.length} failed
                  </span>
                )}
              </>
            ) : (
              <>
                <span className="text-sm font-medium">{selectedFiles.size} selected</span>
                {DOCUMENT_ACTIONS.map((action) => {
                  if (action.requiresOriginal && selectedReconvertable.length === 0) return null;
                  const Icon = action.icon;
                  return (
                    <Button
                      key={action.id}
                      variant={action.variant}
                      size="sm"
                      onClick={bulkHandlers[action.id]}
                      disabled={bulkProgress !== null || isUploading}
                    >
                      <Icon className="h-4 w-4 mr-1" />
                      {action.label}
                    </Button>
                  );
                })}
                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={clearSelection}>
                  <X className="h-4 w-4" />
                </Button>
              </>
            )}
          </div>
        ) : (
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search documents..."
              className="pl-9"
            />
          </div>
        )}
      </div>

      <div className="px-4 pb-4">
        {!hasFetched ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            <div className="mb-3 flex items-center gap-2">
              <Checkbox
                checked={allSelected ? true : someSelected ? "indeterminate" : false}
                onCheckedChange={toggleSelectAll}
              />
              <h3 className="text-sm font-medium text-muted-foreground">
                {isSearching
                  ? `Found ${filteredDocuments.length} of ${documents.length}`
                  : `Your Documents (${documents.length})`}
              </h3>
            </div>
            {isSearching ? renderFlatList() : renderTreeView()}

            {!isSearching && getAllGroups().length > 0 && (
              <>
                <h3 className="mt-6 mb-3 text-sm font-medium text-muted-foreground">
                  Group Knowledge
                </h3>
                <div className="space-y-0.5">
                  {getAllGroups().map((groupId) => (
                    <GroupDocumentsSection
                      key={groupId}
                      groupId={groupId}
                      canWrite={canWriteGroup(groupId)}
                      onInclude={handleInclude}
                      onExclude={handleExclude}
                      onViewFile={handleViewGroupFile}
                    />
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>

      <DocumentDialog
        open={dialogState !== null}
        onOpenChange={(open) => !open && setDialogState(null)}
        filename={dialogState?.filename ?? ""}
        showMetadata={dialogState?.showMetadata}
        editable={dialogState?.editable}
        isNew={dialogState?.isNew}
        onSave={handleSave}
        onRechunk={
          dialogState && !dialogState.isNew && dialogState.editable
            ? async () => {
                await storeRechunk(dialogState.filename, pipelineSpec);
              }
            : undefined
        }
      />

      <MoveDocumentDialog
        open={moveFilePath !== null}
        onOpenChange={(open) => !open && setMoveFilePath(null)}
        currentPath={moveFilePath ?? ""}
        onMove={(destination) => {
          if (moveFilePath) {
            void storeMove(moveFilePath, destination);
          }
        }}
      />

      <MoveDocumentDialog
        open={moveDirPath !== null}
        onOpenChange={(open) => !open && setMoveDirPath(null)}
        currentPath={moveDirPath ?? ""}
        isDirectory
        onMove={(destination) => {
          if (moveDirPath) {
            void storeMoveDir(moveDirPath, destination);
          }
        }}
      />

      <MoveDocumentDialog
        open={bulkMoveFiles !== null}
        onOpenChange={(open) => !open && setBulkMoveFiles(null)}
        bulkFileCount={bulkMoveFiles?.length ?? 0}
        onMove={(dir) => void handleBulkMoveConfirm(dir)}
      />

      <CreateDirectoryDialog
        open={showCreateDir}
        onOpenChange={setShowCreateDir}
        parentPath={createDirParent}
        onCreate={createDir}
      />

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingDelete?.kind === "bulk"
                ? `Delete ${pendingDelete.files.length} documents?`
                : pendingDelete?.kind === "directory"
                  ? "Delete directory?"
                  : "Delete document?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete?.kind === "bulk"
                ? "This will permanently delete the selected documents, their chunks, and any original files. This action cannot be undone."
                : pendingDelete?.kind === "directory"
                  ? `This will permanently delete the directory "${pendingDelete.path}" and all its contents. This action cannot be undone.`
                  : `This will permanently delete "${pendingDelete?.path}" and its chunks. This action cannot be undone.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void confirmDelete()}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={pendingOverwrite !== null}
        onOpenChange={(open) => !open && setPendingOverwrite(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Overwrite existing documents?</AlertDialogTitle>
            <AlertDialogDescription>
              The following documents already exist and will be overwritten:{" "}
              {pendingOverwrite?.conflicting.join(", ")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void confirmOverwrite()}>
              Overwrite
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

interface DocumentCanvasProps {
  onIncludeDocument?: (filename: string) => void;
  onExcludeDocument?: (filename: string) => void;
}

export function DocumentCanvas({ onIncludeDocument, onExcludeDocument }: DocumentCanvasProps) {
  const documentTab = useSettingsStore((state) => state.documentTab);
  const setDocumentTab = useSettingsStore((state) => state.setDocumentTab);

  return (
    <Tabs
      value={documentTab}
      onValueChange={(v) => setDocumentTab(v as "fetched" | "manage")}
      className="h-full gap-0"
    >
      <div className="shrink-0 border-b px-4 flex items-center h-15">
        <TabsList className="w-full sm:w-auto">
          <TabsTrigger value="fetched" className="flex-1 sm:flex-none gap-2">
            <Search className="h-4 w-4" />
            Fetched
          </TabsTrigger>
          <TabsTrigger value="manage" className="flex-1 sm:flex-none gap-2">
            <FolderOpen className="h-4 w-4" />
            Manage
          </TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="fetched" className="min-h-0 overflow-hidden">
        <FetchedDocuments />
      </TabsContent>
      <TabsContent value="manage" className="min-h-0 overflow-hidden">
        <ManageDocuments
          onIncludeDocument={onIncludeDocument}
          onExcludeDocument={onExcludeDocument}
        />
      </TabsContent>
    </Tabs>
  );
}
