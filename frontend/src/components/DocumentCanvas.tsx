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
  fetchDocumentAsset,
  scopePrefix,
  uploadDocument,
} from "../lib/api";
import { DOCUMENT_ACTIONS } from "../lib/document-actions";
import { featureFlags } from "../lib/feature-flags";
import {
  AssetProcessingMode,
  type ChunkingPipeline,
  type ConversionPipeline,
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
import { EMPTY_SCOPE, useDocumentsStore } from "../stores/documents-store";
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
  const fetch = useCallback(() => fetchDocumentAsset(image.filePath), [image.filePath]);
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
  /** Active upload target: "" for personal, else a group id. */
  uploadScope: string;
  /** Groups the user can upload to. */
  writableGroups: string[];
  onUploadScopeChange: (scope: string) => void;
  onConversionPipelineChange: (pipeline: ConversionPipeline) => void;
  onChunkingPipelineChange: (pipeline: ChunkingPipeline) => void;
  onAssetModeChange: (mode: AssetProcessingMode) => void;
}

/** Select sentinel for the personal workspace ("*" can never be a group id). */
const PERSONAL_SCOPE = "*";

function PipelineSettingsBar({
  conversionPipeline,
  chunkingPipeline,
  assetMode,
  isBulkOperating,
  uploadScope,
  writableGroups,
  onUploadScopeChange,
  onConversionPipelineChange,
  onChunkingPipelineChange,
  onAssetModeChange,
}: PipelineSettingsBarProps) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3 border-b px-4 py-3">
      {writableGroups.length > 0 && (
        <div className="flex items-center gap-2">
          <Label
            htmlFor="upload-scope-select"
            className="text-sm text-muted-foreground flex items-center gap-1.5"
          >
            <Upload className="h-4 w-4" />
            Upload to
          </Label>
          <Select
            value={uploadScope || PERSONAL_SCOPE}
            onValueChange={(v) => onUploadScopeChange(v === PERSONAL_SCOPE ? "" : v)}
          >
            <SelectTrigger id="upload-scope-select" className="w-[140px]" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={PERSONAL_SCOPE}>Personal</SelectItem>
              {writableGroups.map((g) => (
                <SelectItem key={g} value={g}>
                  {g}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
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

// --- Per-scope document section ---

/** Edit/view dialog target within a scope (local path). */
interface ScopeDialogState {
  path: string;
  editable: boolean;
}

interface ScopeSectionProps {
  /** Workspace scope: "" for personal, else a group id. */
  scope: string;
  label: string;
  canWrite: boolean;
  /** Whether the section starts expanded (personal) and shows the upload-target hint. */
  defaultOpen: boolean;
  searchQuery: string;
  pipelineSpec: PipelineSpec;
  /** Receives a canonical path (scope-prefixed) for the chat document filter. */
  onIncludeDocument?: (path: string) => void;
  onExcludeDocument?: (path: string) => void;
}

/**
 * Self-contained document manager for one workspace scope. Used identically
 * for the personal store and each group, so the two share one code path. The
 * shared upload area (in ManageDocuments) deposits into the selected scope and
 * the store refresh flows back here through `byScope[scope]`.
 */
function ScopeSection({
  scope,
  label,
  canWrite,
  defaultOpen,
  searchQuery,
  pipelineSpec,
  onIncludeDocument,
  onExcludeDocument,
}: ScopeSectionProps) {
  const state = useDocumentsStore((s) => s.byScope[scope] ?? EMPTY_SCOPE);
  const refresh = useDocumentsStore((s) => s.refresh);
  const removeDoc = useDocumentsStore((s) => s.remove);
  const storeRechunk = useDocumentsStore((s) => s.rechunk);
  const storeReconvert = useDocumentsStore((s) => s.reconvert);
  const storeBulkRechunk = useDocumentsStore((s) => s.bulkRechunk);
  const storeBulkReconvert = useDocumentsStore((s) => s.bulkReconvert);
  const storeBulkDelete = useDocumentsStore((s) => s.bulkDelete);
  const storeMove = useDocumentsStore((s) => s.move);
  const createDir = useDocumentsStore((s) => s.createDir);
  const deleteDir = useDocumentsStore((s) => s.deleteDir);
  const storeMoveDir = useDocumentsStore((s) => s.moveDir);
  const clearError = useDocumentsStore((s) => s.clearError);
  const overrides = useSettingsStore((s) => s.overrides);

  const { documents, directoryTree, mutatingPaths, bulkProgress, operationStage, error, hasFetched } =
    state;

  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [dialog, setDialog] = useState<ScopeDialogState | null>(null);
  const [moveFilePath, setMoveFilePath] = useState<string | null>(null);
  const [moveDirPath, setMoveDirPath] = useState<string | null>(null);
  const [bulkMoveFiles, setBulkMoveFiles] = useState<string[] | null>(null);
  const [createDirParent, setCreateDirParent] = useState<string | undefined>(undefined);
  const [showCreateDir, setShowCreateDir] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [filtered, setFiltered] = useState(documents);
  const [pendingDelete, setPendingDelete] = useState<
    | { kind: "file"; path: string }
    | { kind: "directory"; path: string }
    | { kind: "bulk"; files: string[] }
    | null
  >(null);

  // Refresh this scope's documents and tree once on mount.
  useEffect(() => {
    void refresh(scope);
  }, [refresh, scope]);

  const prefix = scopePrefix(scope);
  const toCanonical = useCallback((path: string) => `${prefix}${path}`, [prefix]);

  const isSearching = searchQuery.trim().length > 0;
  const expanded = isOpen || isSearching;

  useEffect(() => {
    if (!isSearching) {
      setFiltered(documents);
      return;
    }
    let cancelled = false;
    void import("fuse.js").then(({ default: Fuse }) => {
      if (cancelled) return;
      const fuse = new Fuse(documents, { keys: ["display_name", "filename"], threshold: 0.4 });
      setFiltered(fuse.search(searchQuery).map((r) => r.item));
    });
    return () => {
      cancelled = true;
    };
  }, [documents, searchQuery, isSearching]);

  const docsByFilename = useMemo(() => new Map(documents.map((d) => [d.filename, d])), [documents]);

  const clearSelection = useCallback(() => setSelectedFiles(new Set()), []);
  const toggleFile = useCallback((path: string) => {
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);
  const toggleDirFiles = useCallback((paths: string[]) => {
    setSelectedFiles((prev) => {
      const all = paths.every((p) => prev.has(p));
      const next = new Set(prev);
      for (const p of paths) {
        if (all) next.delete(p);
        else next.add(p);
      }
      return next;
    });
  }, []);

  const visibleFilePaths = useMemo(() => {
    if (isSearching) return filtered.map((d) => d.filename);
    if (directoryTree) return collectFilePaths(directoryTree.root);
    return documents.map((d) => d.filename);
  }, [isSearching, filtered, directoryTree, documents]);

  const { allSelected, someSelected } = useMemo(() => {
    let count = 0;
    for (const p of visibleFilePaths) if (selectedFiles.has(p)) count++;
    return {
      allSelected: visibleFilePaths.length > 0 && count === visibleFilePaths.length,
      someSelected: count > 0,
    };
  }, [visibleFilePaths, selectedFiles]);

  const toggleSelectAll = useCallback(() => {
    if (allSelected) clearSelection();
    else setSelectedFiles(new Set(visibleFilePaths));
  }, [allSelected, visibleFilePaths, clearSelection]);

  const selectedReconvertable = useMemo(
    () => [...selectedFiles].filter((f) => docsByFilename.get(f)?.has_original === true),
    [selectedFiles, docsByFilename],
  );

  const handleReconvert = useCallback(
    (path: string) =>
      void storeReconvert(scope, path, { spec: pipelineSpec, llm: buildAuxLlmConfig(overrides) }),
    [storeReconvert, scope, pipelineSpec, overrides],
  );

  const handleDownloadOriginal = useCallback(
    async (path: string) => {
      try {
        const { downloadOriginal } = await import("../lib/api");
        const blob = await downloadOriginal(toCanonical(path));
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = docsByFilename.get(path)?.original_path?.split("/").pop() ?? "original";
        a.click();
        URL.revokeObjectURL(url);
      } catch {
        // silently ignore download errors
      }
    },
    [docsByFilename, toCanonical],
  );

  const handleSave = useCallback(
    async (filename: string, content: string) => {
      const file = new File([content], filename, { type: "text/plain" });
      await uploadDocument(filename, file, { spec: pipelineSpec });
      await refresh(scope);
    },
    [pipelineSpec, scope, refresh],
  );

  const handleBulkMoveConfirm = useCallback(
    async (destinationDir: string) => {
      const files = bulkMoveFiles ?? [];
      setBulkMoveFiles(null);
      clearSelection();
      for (const path of files) {
        const name = path.split("/").pop() ?? path;
        await storeMove(scope, path, destinationDir ? `${destinationDir}/${name}` : name);
      }
    },
    [bulkMoveFiles, clearSelection, storeMove, scope],
  );

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setPendingDelete(null);
    switch (pendingDelete.kind) {
      case "file":
        await removeDoc(scope, pendingDelete.path);
        break;
      case "directory":
        await deleteDir(scope, pendingDelete.path);
        break;
      case "bulk":
        clearSelection();
        await storeBulkDelete(scope, pendingDelete.files);
        break;
    }
  }, [pendingDelete, removeDoc, deleteDir, clearSelection, storeBulkDelete, scope]);

  const bulkHandlers = useMemo<Record<string, () => void>>(
    () => ({
      rechunk: () => {
        const files = [...selectedFiles];
        clearSelection();
        void storeBulkRechunk(scope, files, pipelineSpec);
      },
      reconvert: () => {
        const files = [...selectedReconvertable];
        clearSelection();
        void storeBulkReconvert(scope, files, pipelineSpec, buildAuxLlmConfig(overrides));
      },
      download: () => void selectedReconvertable.reduce(
        (chain, p) => chain.then(() => handleDownloadOriginal(p)),
        Promise.resolve(),
      ),
      move: () => setBulkMoveFiles([...selectedFiles]),
      delete: () => setPendingDelete({ kind: "bulk", files: [...selectedFiles] }),
    }),
    [
      selectedFiles,
      selectedReconvertable,
      clearSelection,
      storeBulkRechunk,
      storeBulkReconvert,
      pipelineSpec,
      overrides,
      handleDownloadOriginal,
      scope,
    ],
  );

  const treeView = () => {
    if (!directoryTree || (directoryTree.total_files === 0 && directoryTree.total_directories === 0)) {
      return <p className="py-2 text-xs text-muted-foreground">No documents in this workspace</p>;
    }
    return (
      <DirectoryTreeView
        entry={directoryTree.root}
        mutatingPaths={mutatingPaths}
        operationStage={operationStage}
        onEditFile={(path) => setDialog({ path, editable: canWrite })}
        onInclude={(path) => onIncludeDocument?.(toCanonical(path))}
        onExclude={(path) => onExcludeDocument?.(toCanonical(path))}
        onFileAction={
          canWrite
            ? (path, actionId) => {
                switch (actionId) {
                  case "rechunk":
                    void storeRechunk(scope, path, pipelineSpec);
                    break;
                  case "reconvert":
                    handleReconvert(path);
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
              }
            : undefined
        }
        onCreateSubdir={canWrite ? (path) => {
          setCreateDirParent(path || undefined);
          setShowCreateDir(true);
        } : undefined}
        onDeleteDir={canWrite ? (path) => setPendingDelete({ kind: "directory", path }) : undefined}
        onMoveDir={canWrite ? (path) => setMoveDirPath(path) : undefined}
        selectedFiles={canWrite ? selectedFiles : undefined}
        onToggleSelectFile={canWrite ? toggleFile : undefined}
        onToggleSelectDir={canWrite ? toggleDirFiles : undefined}
      />
    );
  };

  const flatList = () => {
    if (filtered.length === 0) {
      return <p className="py-2 text-xs text-muted-foreground">No matching documents</p>;
    }
    return (
      <div className="space-y-2">
        {filtered.map((doc) => {
          const docMutating = mutatingPaths.has(doc.filename);
          return (
            <DocumentListItem
              key={doc.filename}
              doc={doc}
              isMutating={docMutating}
              operationStage={docMutating ? operationStage : null}
              onEdit={() => setDialog({ path: doc.filename, editable: canWrite })}
              onIncludeDocument={() => onIncludeDocument?.(toCanonical(doc.filename))}
              onExcludeDocument={() => onExcludeDocument?.(toCanonical(doc.filename))}
              onReconvert={() => handleReconvert(doc.filename)}
              onRemove={() => setPendingDelete({ kind: "file", path: doc.filename })}
              selected={canWrite ? selectedFiles.has(doc.filename) : undefined}
              onToggleSelect={canWrite ? () => toggleFile(doc.filename) : undefined}
            />
          );
        })}
      </div>
    );
  };

  const fileCount = directoryTree?.total_files ?? documents.length;
  const showBulkBar = canWrite && (selectedFiles.size > 0 || bulkProgress !== null);

  return (
    <Collapsible open={expanded} onOpenChange={setIsOpen} className="mb-1">
      <div className="flex items-center gap-2 rounded-md px-1 py-1.5 hover:bg-muted/50 group">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0">
            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </Button>
        </CollapsibleTrigger>
        {scope ? (
          <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <FolderOpen className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <CollapsibleTrigger asChild>
          <button type="button" className="min-w-0 flex-1 truncate text-left text-sm font-medium">
            {label}
          </button>
        </CollapsibleTrigger>
        {scope && (
          <div className="flex gap-0.5 opacity-0 group-hover:opacity-100">
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              title="Include workspace in chat"
              onClick={() => onIncludeDocument?.(`${prefix}`)}
            >
              <Eye className="h-3 w-3" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              title="Exclude workspace from chat"
              onClick={() => onExcludeDocument?.(`${prefix}`)}
            >
              <EyeOff className="h-3 w-3" />
            </Button>
          </div>
        )}
        <Badge variant="secondary" className="shrink-0 text-xs">
          {fileCount}
        </Badge>
      </div>

      <CollapsibleContent className="pl-2">
        {error && (
          <div className="my-2">
            <ErrorBanner message={error} onDismiss={() => clearError(scope)} />
          </div>
        )}
        {!hasFetched ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {showBulkBar ? (
              <div className="flex h-9 items-center gap-2 py-1">
                {bulkProgress ? (
                  <>
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                    <span className="truncate text-sm">{bulkProgress.currentFile}</span>
                    <Progress
                      value={bulkProgress.total > 0 ? (bulkProgress.current / bulkProgress.total) * 100 : 0}
                      className="w-24 shrink-0"
                    />
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {bulkProgress.current}/{bulkProgress.total}
                    </span>
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
                          disabled={bulkProgress !== null}
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
              canWrite &&
              !isSearching &&
              fileCount > 0 && (
                <div className="flex items-center gap-2 py-1">
                  <Checkbox
                    checked={allSelected ? true : someSelected ? "indeterminate" : false}
                    onCheckedChange={toggleSelectAll}
                  />
                  <span className="text-xs text-muted-foreground">Select all</span>
                </div>
              )
            )}
            {isSearching ? flatList() : treeView()}
          </>
        )}
      </CollapsibleContent>

      <DocumentDialog
        open={dialog !== null}
        onOpenChange={(open) => !open && setDialog(null)}
        filename={dialog ? toCanonical(dialog.path) : ""}
        showMetadata={dialog?.editable ?? false}
        editable={dialog?.editable ?? false}
        onSave={handleSave}
        onRechunk={
          dialog && dialog.editable
            ? async () => {
                if (dialog) await storeRechunk(scope, dialog.path, pipelineSpec);
              }
            : undefined
        }
      />
      <MoveDocumentDialog
        open={moveFilePath !== null}
        onOpenChange={(open) => !open && setMoveFilePath(null)}
        currentPath={moveFilePath ?? ""}
        onMove={(destination) => {
          if (moveFilePath) void storeMove(scope, moveFilePath, destination);
        }}
      />
      <MoveDocumentDialog
        open={moveDirPath !== null}
        onOpenChange={(open) => !open && setMoveDirPath(null)}
        currentPath={moveDirPath ?? ""}
        isDirectory
        onMove={(destination) => {
          if (moveDirPath) void storeMoveDir(scope, moveDirPath, destination);
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
        onCreate={(path) => createDir(scope, path)}
      />
      <AlertDialog open={pendingDelete !== null} onOpenChange={(open) => !open && setPendingDelete(null)}>
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
              This action permanently deletes the selected content and its chunks. It cannot be
              undone.
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
    </Collapsible>
  );
}

interface ManageDocumentsProps {
  onIncludeDocument?: (filename: string) => void;
  onExcludeDocument?: (filename: string) => void;
}

function ManageDocuments({ onIncludeDocument, onExcludeDocument }: ManageDocumentsProps) {
  const overrides = useSettingsStore((s) => s.overrides);
  const conversionPipeline = useSettingsStore((s) => s.conversionPipeline);
  const chunkingPipeline = useSettingsStore((s) => s.chunkingPipeline);
  const conversionConfigs = useSettingsStore((s) => s.conversionConfigs);
  const chunkingConfigs = useSettingsStore((s) => s.chunkingConfigs);
  const setConversionPipeline = useSettingsStore((s) => s.setConversionPipeline);
  const setChunkingPipeline = useSettingsStore((s) => s.setChunkingPipeline);
  const assetMode = useSettingsStore((s) => s.assetMode);
  const setAssetMode = useSettingsStore((s) => s.setAssetMode);

  const upload = useDocumentsStore((s) => s.upload);
  const uploadMultiple = useDocumentsStore((s) => s.uploadMultiple);
  const uploadCol = useDocumentsStore((s) => s.uploadCol);
  const createDir = useDocumentsStore((s) => s.createDir);
  const refresh = useDocumentsStore((s) => s.refresh);

  const groups = useMemo(() => getAllGroups(), []);
  const writableGroups = useMemo(() => groups.filter((g) => canWriteGroup(g)), [groups]);

  const [uploadScope, setUploadScope] = useState("");
  const target = useDocumentsStore((s) => s.byScope[uploadScope] ?? EMPTY_SCOPE);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isPreparing, setIsPreparing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [newDocOpen, setNewDocOpen] = useState(false);
  const [showCreateDir, setShowCreateDir] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [pendingOverwrite, setPendingOverwrite] = useState<{
    files: File[];
    conflicting: string[];
  } | null>(null);

  const beginOp = useCallback((): AbortSignal => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    return ctrl.signal;
  }, []);
  const handleCancel = useCallback(() => abortRef.current?.abort(), []);

  const pipelineSpec: PipelineSpec = useMemo(
    () =>
      featureFlags.pipelineSpec
        ? {
            conversion: {
              pipeline: conversionPipeline,
              config: conversionConfigs[conversionPipeline],
            },
            chunking: { pipeline: chunkingPipeline, config: chunkingConfigs[chunkingPipeline] },
            process_assets: assetMode,
          }
        : { process_assets: assetMode },
    [conversionPipeline, chunkingPipeline, conversionConfigs, chunkingConfigs, assetMode],
  );

  const uploadOptions = useMemo<UploadDocumentOptions>(
    () => ({ spec: pipelineSpec, llm: buildAuxLlmConfig(overrides), scope: uploadScope }),
    [pipelineSpec, overrides, uploadScope],
  );

  const uploadInFlight = isPreparing || target.isUploading;
  useEffect(() => {
    if (!uploadInFlight) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [uploadInFlight]);

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const fileArray = Array.from(files);
      setUploadError(null);

      const stems = fileArray.map((f) => fileStem(f.name));
      const seen = new Set<string>();
      for (const stem of stems) {
        if (seen.has(stem)) {
          setUploadError(`Batch contains files with the same stem "${stem}"`);
          return;
        }
        seen.add(stem);
      }

      const existingStems = new Set(target.documents.map((d) => fileStem(d.filename)));
      const conflicting = stems.filter((s) => existingStems.has(s));
      if (conflicting.length > 0) {
        setPendingOverwrite({ files: fileArray, conflicting });
        return;
      }

      const signal = beginOp();
      if (fileArray.length === 1) {
        await upload(uploadScope, fileArray[0], { ...uploadOptions, signal });
      } else {
        await uploadMultiple(uploadScope, fileArray, { ...uploadOptions, signal });
      }
    },
    [beginOp, upload, uploadMultiple, uploadOptions, uploadScope, target.documents],
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
        await uploadCol(uploadScope, collection, { ...uploadOptions, signal });
      } catch (err) {
        if (!isAbortError(err)) throw err;
      } finally {
        setIsPreparing(false);
      }
    },
    [beginOp, handleFiles, uploadCol, uploadOptions, uploadScope],
  );

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      void handleFiles(e.target.files);
      if (fileInputRef.current) fileInputRef.current.value = "";
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
        await uploadCol(uploadScope, collection, { ...uploadOptions, signal });
      } catch (err) {
        if (!isAbortError(err)) throw err;
      } finally {
        setIsPreparing(false);
        if (directoryInputRef.current) directoryInputRef.current.value = "";
      }
    },
    [beginOp, uploadCol, uploadOptions, uploadScope],
  );

  const handleZipInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;
      const file = files[0];
      if (!file.name.toLowerCase().endsWith(".zip")) return;
      const signal = beginOp();
      try {
        await uploadCol(uploadScope, file, { ...uploadOptions, signal });
      } catch (err) {
        if (!isAbortError(err)) throw err;
      } finally {
        if (zipInputRef.current) zipInputRef.current.value = "";
      }
    },
    [beginOp, uploadCol, uploadOptions, uploadScope],
  );

  const confirmOverwrite = useCallback(async () => {
    if (!pendingOverwrite) return;
    const { files } = pendingOverwrite;
    setPendingOverwrite(null);
    const signal = beginOp();
    const opts = { ...uploadOptions, overwrite: true, signal };
    if (files.length === 1) {
      await upload(uploadScope, files[0], opts);
    } else {
      await uploadMultiple(uploadScope, files, opts);
    }
  }, [beginOp, pendingOverwrite, upload, uploadMultiple, uploadOptions, uploadScope]);

  const handleSaveNew = useCallback(
    async (filename: string, content: string) => {
      const file = new File([content], filename, { type: "text/plain" });
      await uploadDocument(filename, file, { spec: pipelineSpec, scope: uploadScope });
      await refresh(uploadScope);
      setNewDocOpen(false);
    },
    [pipelineSpec, uploadScope, refresh],
  );

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {uploadError && <ErrorBanner message={uploadError} onDismiss={() => setUploadError(null)} />}

      <UploadArea
        isDragging={isDragging}
        isUploading={target.isUploading}
        isPreparing={isPreparing}
        uploadProgress={target.uploadProgress}
        operationStage={target.operationStage}
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
        onNewDocument={() => setNewDocOpen(true)}
        onNewFolder={() => setShowCreateDir(true)}
        onCancel={handleCancel}
      />

      <PipelineSettingsBar
        conversionPipeline={conversionPipeline}
        chunkingPipeline={chunkingPipeline}
        assetMode={assetMode}
        isBulkOperating={false}
        uploadScope={uploadScope}
        writableGroups={writableGroups}
        onUploadScopeChange={setUploadScope}
        onConversionPipelineChange={setConversionPipeline}
        onChunkingPipelineChange={setChunkingPipeline}
        onAssetModeChange={setAssetMode}
      />

      <div className="p-4 pb-2">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search documents..."
            className="pl-9"
          />
        </div>
      </div>

      <div className="space-y-1 px-4 pb-4">
        <ScopeSection
          scope=""
          label="Your Documents"
          canWrite
          defaultOpen
          searchQuery={searchQuery}
          pipelineSpec={pipelineSpec}
          onIncludeDocument={onIncludeDocument}
          onExcludeDocument={onExcludeDocument}
        />
        {groups.map((groupId) => (
          <ScopeSection
            key={groupId}
            scope={groupId}
            label={groupId}
            canWrite={canWriteGroup(groupId)}
            defaultOpen={false}
            searchQuery={searchQuery}
            pipelineSpec={pipelineSpec}
            onIncludeDocument={onIncludeDocument}
            onExcludeDocument={onExcludeDocument}
          />
        ))}
      </div>

      <DocumentDialog
        open={newDocOpen}
        onOpenChange={(open) => !open && setNewDocOpen(false)}
        filename="new-document.md"
        editable
        isNew
        onSave={handleSaveNew}
      />

      <CreateDirectoryDialog
        open={showCreateDir}
        onOpenChange={setShowCreateDir}
        onCreate={(path) => createDir(uploadScope, path)}
      />

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
