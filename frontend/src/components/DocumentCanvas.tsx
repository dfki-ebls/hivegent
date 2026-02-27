import Fuse from "fuse.js";
import JSZip from "jszip";
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
  buildLlmConfig,
  getGroupDirectoryTree,
  getGroupDocumentContent,
  uploadDocument,
} from "../lib/api";
import {
  type ChunkingPipeline,
  type ConversionPipeline,
  type DirectoryTreeResponse,
  type DocumentInfo,
  type FetchedChunk,
  type FetchedDocument,
  type PipelineSpec,
  chunkPositionLabel,
  sortChunks,
} from "../lib/types";
import { useFetchedDocumentsStore } from "../stores/fetched-documents-store";
import { useUserDocumentsStore } from "../stores/user-documents-store";
import { canWriteGroup, getAllGroups, useSettingsStore } from "../stores/settings-store";
import { ChunkingPipelineSelector } from "./ChunkingPipelineSelector";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "./ui/collapsible";
import { ConversionPipelineSelector } from "./ConversionPipelineSelector";
import { CreateDirectoryDialog } from "./CreateDirectoryDialog";
import { DirectoryTreeView } from "./DirectoryTreeView";
import { DocumentDialog } from "./DocumentDialog";
import { MoveDocumentDialog } from "./MoveDocumentDialog";
import { Alert, AlertDescription } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { ScrollArea } from "./ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

// --- Utility functions ---

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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
      <pre className="line-clamp-4 whitespace-pre-wrap text-xs text-muted-foreground">
        {chunk.content}
      </pre>
    </button>
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
            <ChevronRight className={`h-4 w-4 transition-transform ${open ? "rotate-90" : ""}`} />
          </Button>
        </CollapsibleTrigger>
        <button
          type="button"
          className="truncate text-sm font-medium hover:underline text-left min-w-0"
          onClick={() => onFilenameClick(doc.filename)}
          title={doc.filename}
        >
          {doc.filename}
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
  isLoading: boolean;
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
}

function UploadArea({
  isDragging,
  isLoading,
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
}: UploadAreaProps) {
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
        <Upload className="h-10 w-10 text-muted-foreground" />
        <div className="text-center">
          <p className="font-medium">Drop files here to upload</p>
          <p className="text-sm text-muted-foreground">or click to browse</p>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={onFileInputChange}
        />
        <input
          ref={directoryInputRef}
          type="file"
          // @ts-expect-error webkitdirectory is not in React's type definitions
          webkitdirectory=""
          multiple
          className="hidden"
          onChange={onDirectoryInputChange}
        />
        <input
          ref={zipInputRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={onZipInputChange}
        />
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={onSelectFiles} disabled={isLoading}>
            <Paperclip className="h-4 w-4 mr-1" />
            Select Files
          </Button>
          <Button variant="secondary" size="sm" onClick={onSelectDirectory} disabled={isLoading}>
            <FolderOpen className="h-4 w-4 mr-1" />
            Upload Folder
          </Button>
          <Button variant="secondary" size="sm" onClick={onSelectZip} disabled={isLoading}>
            <Archive className="h-4 w-4 mr-1" />
            Upload ZIP
          </Button>
        </div>
        <div className="flex flex-col items-center gap-2 pt-4 border-t border-muted-foreground/15 w-full">
          <p className="text-xs text-muted-foreground">
            Or create and edit documents directly in the browser
          </p>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onNewDocument} disabled={isLoading}>
              <Plus className="h-4 w-4 mr-1" />
              New Document
            </Button>
            <Button variant="outline" size="sm" onClick={onNewFolder} disabled={isLoading}>
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
  isLoading: boolean;
  onConversionPipelineChange: (pipeline: ConversionPipeline) => void;
  onChunkingPipelineChange: (pipeline: ChunkingPipeline) => void;
}

function PipelineSettingsBar({
  conversionPipeline,
  chunkingPipeline,
  isLoading,
  onConversionPipelineChange,
  onChunkingPipelineChange,
}: PipelineSettingsBarProps) {
  return (
    <div className="flex items-center justify-center gap-8 border-b px-4 py-3">
      <ConversionPipelineSelector
        value={conversionPipeline}
        onChange={onConversionPipelineChange}
        disabled={isLoading}
      />
      <ChunkingPipelineSelector
        value={chunkingPipeline}
        onChange={onChunkingPipelineChange}
        disabled={isLoading}
      />
    </div>
  );
}

interface DocumentListItemProps {
  doc: DocumentInfo;
  isLoading: boolean;
  onEdit: () => void;
  onIncludeDocument: () => void;
  onExcludeDocument: () => void;
  onReconvert: () => void;
  onRemove: () => void;
}

function DocumentListItem({
  doc,
  isLoading,
  onEdit,
  onIncludeDocument,
  onExcludeDocument,
  onReconvert,
  onRemove,
}: DocumentListItemProps) {
  return (
    <button
      type="button"
      className="flex w-full items-center gap-3 rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50 cursor-pointer text-left"
      onClick={onEdit}
    >
      <FileText className="h-8 w-8 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate font-medium text-sm">{doc.filename}</p>
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
          disabled={isLoading}
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
        disabled={isLoading}
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
        disabled={isLoading}
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
        disabled={isLoading}
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
    getGroupDirectoryTree(groupId)
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
      <div className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted/50 group">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0">
            {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </Button>
        </CollapsibleTrigger>
        <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
        <CollapsibleTrigger asChild>
          <button type="button" className="min-w-0 flex-1 truncate text-sm font-medium text-left">
            {groupId}
          </button>
        </CollapsibleTrigger>
        {tree && (
          <Badge variant="secondary" className="shrink-0 text-xs">
            {tree.total_files}
          </Badge>
        )}
        <div className="hidden gap-0.5 group-hover:flex">
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
              isLoading={false}
              onEditFile={(path) => onViewFile(groupId, path)}
              onInclude={handleInclude}
              onExclude={handleExclude}
              onRemoveFile={
                canWrite && onRemoveFile ? (path) => onRemoveFile(groupId, path) : undefined
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
  /** Custom content fetcher (for group documents). */
  getContent?: (filename: string) => Promise<string>;
}

interface ManageDocumentsProps {
  onIncludeDocument?: (filename: string) => void;
  onExcludeDocument?: (filename: string) => void;
}

function ManageDocuments({ onIncludeDocument, onExcludeDocument }: ManageDocumentsProps) {
  const {
    documents,
    directoryTree,
    isLoading,
    error,
    fetchDocuments,
    fetchDirectoryTree,
    upload,
    uploadCol,
    remove,
    rechunk: storeRechunk,
    reconvert: storeReconvert,
    move: storeMove,
    createDir,
    deleteDir,
    clearError,
  } = useUserDocumentsStore();
  const llmSettings = useSettingsStore((state) => state.llm);
  const visionModel = useSettingsStore((state) => state.visionModel);
  const conversionPipeline = useSettingsStore((state) => state.conversionPipeline);
  const chunkingPipeline = useSettingsStore((state) => state.chunkingPipeline);
  const conversionConfigs = useSettingsStore((state) => state.conversionConfigs);
  const chunkingConfigs = useSettingsStore((state) => state.chunkingConfigs);
  const setConversionPipeline = useSettingsStore((state) => state.setConversionPipeline);
  const setChunkingPipeline = useSettingsStore((state) => state.setChunkingPipeline);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [dialogState, setDialogState] = useState<DialogState | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [moveFilePath, setMoveFilePath] = useState<string | null>(null);
  const [createDirParent, setCreateDirParent] = useState<string | undefined>(undefined);
  const [showCreateDir, setShowCreateDir] = useState(false);

  const fuse = useMemo(
    () => new Fuse(documents, { keys: ["filename"], threshold: 0.4 }),
    [documents],
  );

  const filteredDocuments = useMemo(() => {
    if (!searchQuery.trim()) return documents;
    return fuse.search(searchQuery).map((result) => result.item);
  }, [documents, searchQuery, fuse]);

  const pipelineSpec: PipelineSpec = useMemo(
    () => ({
      conversion: {
        pipeline: conversionPipeline,
        config: conversionConfigs[conversionPipeline],
      },
      chunking: {
        pipeline: chunkingPipeline,
        config: chunkingConfigs[chunkingPipeline],
      },
    }),
    [conversionPipeline, chunkingPipeline, conversionConfigs, chunkingConfigs],
  );

  useEffect(() => {
    void fetchDocuments();
    void fetchDirectoryTree();
  }, [fetchDocuments, fetchDirectoryTree]);

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
      filename: filepath,
      showMetadata: false,
      editable: false,
      isNew: false,
      getContent: (f) => getGroupDocumentContent(groupId, f),
    });
  }, []);

  const handleSave = useCallback(
    async (filename: string, content: string) => {
      const file = new File([content], filename, { type: "text/plain" });
      await uploadDocument(filename, file, { spec: pipelineSpec });
      await fetchDocuments();
      await fetchDirectoryTree();
    },
    [fetchDocuments, fetchDirectoryTree, pipelineSpec],
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
        llm: buildLlmConfig({
          model: visionModel,
          apiKey: llmSettings.apiKey,
          baseUrl: llmSettings.baseUrl,
        }),
      });
    },
    [storeReconvert, pipelineSpec, visionModel, llmSettings],
  );

  // --- File upload handlers ---

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const options = {
        spec: pipelineSpec,
        llm: buildLlmConfig({
          model: visionModel,
          apiKey: llmSettings.apiKey,
          baseUrl: llmSettings.baseUrl,
        }),
      };
      for (const file of Array.from(files)) {
        await upload(file, options);
      }
    },
    [upload, pipelineSpec, visionModel, llmSettings],
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
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      void handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
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

      const options = {
        spec: pipelineSpec,
        llm: buildLlmConfig({
          model: visionModel,
          apiKey: llmSettings.apiKey,
          baseUrl: llmSettings.baseUrl,
        }),
      };

      // Bundle directory files into a ZIP using JSZip
      const zip = new JSZip();
      for (const file of Array.from(files)) {
        const relativePath = file.webkitRelativePath || file.name;
        zip.file(relativePath, file);
      }
      const blob = await zip.generateAsync({ type: "blob" });
      const zipFile = new File([blob], "collection.zip", {
        type: "application/zip",
      });

      await uploadCol(zipFile, options);

      if (directoryInputRef.current) {
        directoryInputRef.current.value = "";
      }
    },
    [uploadCol, pipelineSpec, visionModel, llmSettings],
  );

  const handleZipInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;

      const file = files[0];
      if (!file.name.toLowerCase().endsWith(".zip")) return;

      const options = {
        spec: pipelineSpec,
        llm: buildLlmConfig({
          model: visionModel,
          apiKey: llmSettings.apiKey,
          baseUrl: llmSettings.baseUrl,
        }),
      };

      await uploadCol(file, options);

      if (zipInputRef.current) {
        zipInputRef.current.value = "";
      }
    },
    [uploadCol, pipelineSpec, visionModel, llmSettings],
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

  const isSearching = searchQuery.trim().length > 0;

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
        {filteredDocuments.map((doc) => (
          <DocumentListItem
            key={doc.filename}
            doc={doc}
            isLoading={isLoading}
            onEdit={() => handleEdit(doc.filename)}
            onIncludeDocument={() => handleInclude(doc.filename)}
            onExcludeDocument={() => handleExclude(doc.filename)}
            onReconvert={() => handleReconvert(doc.filename)}
            onRemove={() => remove(doc.filename)}
          />
        ))}
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
        isLoading={isLoading}
        onEditFile={handleEdit}
        onInclude={handleInclude}
        onExclude={handleExclude}
        onReconvert={handleReconvert}
        onRemoveFile={(path) => remove(path)}
        onMoveFile={(path) => setMoveFilePath(path)}
        onCreateSubdir={handleCreateSubdir}
        onDeleteDir={(path) => deleteDir(path)}
      />
    );
  };

  return (
    <div className="flex h-full flex-col">
      {error && <ErrorBanner message={error} onDismiss={clearError} />}

      <UploadArea
        isDragging={isDragging}
        isLoading={isLoading}
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
      />

      <PipelineSettingsBar
        conversionPipeline={conversionPipeline}
        chunkingPipeline={chunkingPipeline}
        isLoading={isLoading}
        onConversionPipelineChange={setConversionPipeline}
        onChunkingPipelineChange={setChunkingPipeline}
      />

      <div className="flex-1 flex flex-col min-h-0">
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
        <ScrollArea className="flex-1">
          <div className="px-4 pb-4">
            <h3 className="mb-3 text-sm font-medium text-muted-foreground">
              {isSearching
                ? `Found ${filteredDocuments.length} of ${documents.length}`
                : `Your Documents (${documents.length})`}
            </h3>
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
          </div>
        </ScrollArea>
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
        getContent={dialogState?.getContent}
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

      <CreateDirectoryDialog
        open={showCreateDir}
        onOpenChange={setShowCreateDir}
        parentPath={createDirParent}
        onCreate={createDir}
      />
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
