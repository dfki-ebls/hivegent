import Fuse from "fuse.js";
import JSZip from "jszip";
import {
  AlertCircle,
  Archive,
  EyeOff,
  FileText,
  FolderOpen,
  FolderPlus,
  MessageSquarePlus,
  Plus,
  RefreshCw,
  RotateCcw,
  Scissors,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  buildLlmConfig,
  getDocumentContent,
  requiresConversion,
  uploadDocument,
} from "../lib/api";
import type {
  ChunkingPipeline,
  ConversionPipeline,
  DocumentInfo,
  StoredDocument,
} from "../lib/types";
import { useFetchedDocumentsStore } from "../stores/fetched-documents-store";
import { useManagedDocumentsStore } from "../stores/managed-documents-store";
import { useSettingsStore } from "../stores/settings-store";
import { ChunkingPipelineSelector } from "./ChunkingPipelineSelector";
import { ChunkViewerDialog } from "./ChunkViewerDialog";
import { ConversionPipelineSelector } from "./ConversionPipelineSelector";
import { CreateDirectoryDialog } from "./CreateDirectoryDialog";
import { DirectoryTreeView } from "./DirectoryTreeView";
import { DocumentPreviewDialog } from "./DocumentPreviewDialog";
import { MoveDocumentDialog } from "./MoveDocumentDialog";
import { Alert, AlertDescription } from "./ui/alert";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
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

interface DocumentCardProps {
  doc: StoredDocument;
  onClick: () => void;
}

function DocumentCard({ doc, onClick }: DocumentCardProps) {
  return (
    <Card
      className="flex cursor-pointer flex-col transition-colors hover:bg-muted/50"
      onClick={onClick}
    >
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="line-clamp-1 text-sm">{doc.filename}</CardTitle>
          {doc.score !== undefined && (
            <Badge variant="secondary" className="shrink-0">
              {(doc.score * 100).toFixed(1)}%
            </Badge>
          )}
        </div>
        <div className="flex flex-wrap gap-1">
          {doc.sources.map((source) => (
            <Badge key={source} variant="outline" className="text-xs">
              {source}
            </Badge>
          ))}
        </div>
      </CardHeader>
      <CardContent className="flex-1">
        <pre className="line-clamp-6 whitespace-pre-wrap text-xs text-muted-foreground">
          {doc.content}
        </pre>
      </CardContent>
    </Card>
  );
}

function FetchedDocuments() {
  const documents = useFetchedDocumentsStore((state) => state.documents);
  const [selectedDocument, setSelectedDocument] =
    useState<StoredDocument | null>(null);

  const documentList = useMemo(
    () =>
      Array.from(documents.values()).sort(
        (a, b) => (b.score ?? 0) - (a.score ?? 0),
      ),
    [documents],
  );

  if (documentList.length === 0) {
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
        <div className="grid gap-3 p-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
          {documentList.map((doc) => (
            <DocumentCard
              key={doc.filename}
              doc={doc}
              onClick={() => setSelectedDocument(doc)}
            />
          ))}
        </div>
      </ScrollArea>

      <DocumentPreviewDialog
        open={selectedDocument !== null}
        onOpenChange={(open) => !open && setSelectedDocument(null)}
        filename={selectedDocument?.filename ?? ""}
        content={selectedDocument?.content ?? null}
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
        <Button
          variant="ghost"
          size="sm"
          className="h-auto p-1"
          onClick={onDismiss}
        >
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
      {/* biome-ignore lint/a11y/noStaticElementInteractions: drag-and-drop zone */}
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
          <Button
            variant="secondary"
            size="sm"
            onClick={onSelectFiles}
            disabled={isLoading}
          >
            Select Files
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={onSelectDirectory}
            disabled={isLoading}
          >
            <FolderOpen className="h-4 w-4 mr-1" />
            Upload Folder
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={onSelectZip}
            disabled={isLoading}
          >
            <Archive className="h-4 w-4 mr-1" />
            Upload ZIP
          </Button>
        </div>
        <div className="flex flex-col items-center gap-2 pt-4 border-t border-muted-foreground/15 w-full">
          <p className="text-xs text-muted-foreground">
            Or create and edit documents directly in the browser
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={onNewDocument}
              disabled={isLoading}
            >
              <Plus className="h-4 w-4 mr-1" />
              New Document
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onNewFolder}
              disabled={isLoading}
            >
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
  onViewChunks: () => void;
  onRechunk: () => void;
  onReconvert: () => void;
  onRemove: () => void;
}

function DocumentListItem({
  doc,
  isLoading,
  onEdit,
  onIncludeDocument,
  onExcludeDocument,
  onViewChunks,
  onRechunk,
  onReconvert,
  onRemove,
}: DocumentListItemProps) {
  return (
    // biome-ignore lint/a11y/useSemanticElements: contains nested interactive elements
    <div
      role="button"
      tabIndex={0}
      className="flex items-center gap-3 rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50 cursor-pointer"
      onClick={onEdit}
      onKeyDown={(e) => {
        if (e.key === "Enter") onEdit();
      }}
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
          {formatFileSize(doc.size_bytes)} ·{" "}
          {formatRelativeDate(doc.modified_at)}
        </p>
      </div>
      {doc.chunk_count != null && (
        <Button
          variant="ghost"
          size="icon"
          title="View chunks"
          onClick={(e) => {
            e.stopPropagation();
            onViewChunks();
          }}
          disabled={isLoading}
        >
          <Scissors className="h-4 w-4" />
        </Button>
      )}
      {doc.chunk_count != null && (
        <Button
          variant="ghost"
          size="icon"
          title="Rechunk"
          onClick={(e) => {
            e.stopPropagation();
            onRechunk();
          }}
          disabled={isLoading}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      )}
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
        <MessageSquarePlus className="h-4 w-4" />
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
    </div>
  );
}

interface EditorState {
  filename: string;
  content: string | null;
  isNew: boolean;
  isLoading: boolean;
}

interface ManageDocumentsProps {
  onIncludeDocument?: (filename: string) => void;
  onExcludeDocument?: (filename: string) => void;
}

function ManageDocuments({
  onIncludeDocument,
  onExcludeDocument,
}: ManageDocumentsProps) {
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
  } = useManagedDocumentsStore();
  const llmSettings = useSettingsStore((state) => state.llm);
  const visionModel = useSettingsStore((state) => state.visionModel);
  const conversionPipeline = useSettingsStore(
    (state) => state.conversionPipeline,
  );
  const chunkingPipeline = useSettingsStore((state) => state.chunkingPipeline);
  const setConversionPipeline = useSettingsStore(
    (state) => state.setConversionPipeline,
  );
  const setChunkingPipeline = useSettingsStore(
    (state) => state.setChunkingPipeline,
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [chunkViewerFilename, setChunkViewerFilename] = useState<string | null>(
    null,
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [moveFilePath, setMoveFilePath] = useState<string | null>(null);
  const [createDirParent, setCreateDirParent] = useState<string | undefined>(
    undefined,
  );
  const [showCreateDir, setShowCreateDir] = useState(false);

  const fuse = useMemo(
    () => new Fuse(documents, { keys: ["filename"], threshold: 0.4 }),
    [documents],
  );

  const filteredDocuments = useMemo(() => {
    if (!searchQuery.trim()) return documents;
    return fuse.search(searchQuery).map((result) => result.item);
  }, [documents, searchQuery, fuse]);

  useEffect(() => {
    fetchDocuments();
    fetchDirectoryTree();
  }, [fetchDocuments, fetchDirectoryTree]);

  // --- Editor handlers ---

  const handleEdit = useCallback(async (filepath: string) => {
    setEditor({
      filename: filepath,
      content: null,
      isNew: false,
      isLoading: true,
    });
    try {
      const content = await getDocumentContent(filepath);
      setEditor({
        filename: filepath,
        content,
        isNew: false,
        isLoading: false,
      });
    } catch {
      setEditor({
        filename: filepath,
        content: "Failed to load document content",
        isNew: false,
        isLoading: false,
      });
    }
  }, []);

  const handleNew = useCallback(() => {
    setEditor({
      filename: "new-document.md",
      content: "",
      isNew: true,
      isLoading: false,
    });
  }, []);

  const handleSave = useCallback(
    async (filename: string, content: string) => {
      const file = new File([content], filename, { type: "text/plain" });
      await uploadDocument(filename, file, { chunkingPipeline });
      await fetchDocuments();
      await fetchDirectoryTree();
    },
    [fetchDocuments, fetchDirectoryTree, chunkingPipeline],
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

  const handleRechunk = useCallback(
    async (filepath: string) => {
      await storeRechunk(filepath, chunkingPipeline);
    },
    [storeRechunk, chunkingPipeline],
  );

  const handleReconvert = useCallback(
    async (filepath: string) => {
      await storeReconvert(filepath, {
        conversionPipeline,
        chunkingPipeline,
        llm: buildLlmConfig({
          model: visionModel,
          apiKey: llmSettings.apiKey,
          baseUrl: llmSettings.baseUrl,
        }),
      });
    },
    [
      storeReconvert,
      conversionPipeline,
      chunkingPipeline,
      visionModel,
      llmSettings,
    ],
  );

  // --- File upload handlers ---

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      for (const file of Array.from(files)) {
        const options = requiresConversion(file.name)
          ? {
              conversionPipeline,
              chunkingPipeline,
              llm: buildLlmConfig({
                model: visionModel,
                apiKey: llmSettings.apiKey,
                baseUrl: llmSettings.baseUrl,
              }),
            }
          : { chunkingPipeline };
        await upload(file, options);
      }
    },
    [upload, conversionPipeline, chunkingPipeline, visionModel, llmSettings],
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
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
  );

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      handleFiles(e.target.files);
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
        conversionPipeline,
        chunkingPipeline,
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
    [uploadCol, conversionPipeline, chunkingPipeline, visionModel, llmSettings],
  );

  const handleZipInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files || files.length === 0) return;

      const file = files[0];
      if (!file.name.toLowerCase().endsWith(".zip")) return;

      const options = {
        conversionPipeline,
        chunkingPipeline,
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
    [uploadCol, conversionPipeline, chunkingPipeline, visionModel, llmSettings],
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
            onViewChunks={() => setChunkViewerFilename(doc.filename)}
            onRechunk={() => handleRechunk(doc.filename)}
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

    if (
      directoryTree.total_files === 0 &&
      directoryTree.total_directories === 0
    ) {
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
        onViewChunks={(path) => setChunkViewerFilename(path)}
        onRechunk={handleRechunk}
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
          </div>
        </ScrollArea>
      </div>

      <DocumentPreviewDialog
        open={editor !== null}
        onOpenChange={(open) => !open && setEditor(null)}
        filename={editor?.filename ?? ""}
        content={editor?.content ?? null}
        isLoading={editor?.isLoading ?? false}
        editable
        onSave={handleSave}
      />

      <ChunkViewerDialog
        open={chunkViewerFilename !== null}
        onOpenChange={(open) => !open && setChunkViewerFilename(null)}
        filename={chunkViewerFilename ?? ""}
        onRechunk={async () => {
          if (chunkViewerFilename) {
            await storeRechunk(chunkViewerFilename, chunkingPipeline);
          }
        }}
      />

      <MoveDocumentDialog
        open={moveFilePath !== null}
        onOpenChange={(open) => !open && setMoveFilePath(null)}
        currentPath={moveFilePath ?? ""}
        onMove={(destination) => {
          if (moveFilePath) {
            storeMove(moveFilePath, destination);
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

export function DocumentCanvas({
  onIncludeDocument,
  onExcludeDocument,
}: DocumentCanvasProps) {
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
