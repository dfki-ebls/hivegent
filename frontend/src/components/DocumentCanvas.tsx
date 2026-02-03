import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import Fuse from 'fuse.js';
import { AlertCircle, FileText, FolderOpen, MessageSquarePlus, Plus, Search, Trash2, Upload, X } from 'lucide-react';

import { getDocumentContent, uploadDocument } from '../lib/api';
import type { DocumentInfo, StoredDocument } from '../lib/types';
import { FileExtension } from '../lib/types';
import { useFetchedDocumentsStore } from '../stores/fetched-documents-store';
import { useManagedDocumentsStore } from '../stores/managed-documents-store';
import { DocumentPreviewDialog } from './DocumentPreviewDialog';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Input } from './ui/input';
import { ScrollArea } from './ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';

// --- Utility functions ---

const ALLOWED_EXTENSIONS = Object.values(FileExtension);

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

  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
  return date.toLocaleDateString();
}

function isValidFile(filename: string): boolean {
  const ext = '.' + filename.split('.').pop()?.toLowerCase();
  return ALLOWED_EXTENSIONS.includes(ext as FileExtension);
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
  const [selectedDocument, setSelectedDocument] = useState<StoredDocument | null>(null);

  const documentList = useMemo(
    () => Array.from(documents.values()).sort((a, b) => (b.score ?? 0) - (a.score ?? 0)),
    [documents]
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
        filename={selectedDocument?.filename ?? ''}
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
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onFileInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onSelectFiles: () => void;
  onNewDocument: () => void;
}

function UploadArea({
  isDragging,
  isLoading,
  fileInputRef,
  onDragOver,
  onDragLeave,
  onDrop,
  onFileInputChange,
  onSelectFiles,
  onNewDocument,
}: UploadAreaProps) {
  return (
    <div className="border-b p-4">
      <div
        className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-6 transition-colors ${
          isDragging
            ? 'border-primary bg-primary/10'
            : 'border-muted-foreground/25 bg-muted/25 hover:border-muted-foreground/50 hover:bg-muted/50'
        }`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <Upload className="h-10 w-10 text-muted-foreground" />
        <div className="text-center">
          <p className="font-medium">Drop files here to upload</p>
          <p className="text-sm text-muted-foreground">
            or click to browse ({ALLOWED_EXTENSIONS.join(', ')})
          </p>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept={ALLOWED_EXTENSIONS.join(',')}
          multiple
          className="hidden"
          onChange={onFileInputChange}
        />
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={onSelectFiles} disabled={isLoading}>
            Select Files
          </Button>
          <Button variant="outline" size="sm" onClick={onNewDocument} disabled={isLoading}>
            <Plus className="h-4 w-4 mr-1" />
            New Document
          </Button>
        </div>
      </div>
    </div>
  );
}

interface DocumentListItemProps {
  doc: DocumentInfo;
  isLoading: boolean;
  onEdit: () => void;
  onSendToChat: () => void;
  onRemove: () => void;
}

function DocumentListItem({ doc, isLoading, onEdit, onSendToChat, onRemove }: DocumentListItemProps) {
  return (
    <div
      className="flex items-center gap-3 rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50 cursor-pointer"
      onClick={onEdit}
    >
      <FileText className="h-8 w-8 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium text-sm">{doc.filename}</p>
        <p className="text-xs text-muted-foreground">
          {formatFileSize(doc.size_bytes)} · {formatRelativeDate(doc.modified_at)}
        </p>
      </div>
      <Button
        variant="ghost"
        size="icon"
        title="Send to chat"
        onClick={(e) => {
          e.stopPropagation();
          onSendToChat();
        }}
        disabled={isLoading}
      >
        <MessageSquarePlus className="h-4 w-4" />
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
  onSendToChat?: (content: string) => void;
}

function ManageDocuments({ onSendToChat }: ManageDocumentsProps) {
  const { documents, isLoading, error, fetchDocuments, upload, remove, clearError } =
    useManagedDocumentsStore();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const fuse = useMemo(
    () => new Fuse(documents, { keys: ['filename'], threshold: 0.4 }),
    [documents]
  );

  const filteredDocuments = useMemo(() => {
    if (!searchQuery.trim()) return documents;
    return fuse.search(searchQuery).map((result) => result.item);
  }, [documents, searchQuery, fuse]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  // --- Editor handlers ---

  const handleEdit = useCallback(async (doc: DocumentInfo) => {
    setEditor({ filename: doc.filename, content: null, isNew: false, isLoading: true });
    try {
      const content = await getDocumentContent(doc.filename);
      setEditor({ filename: doc.filename, content, isNew: false, isLoading: false });
    } catch {
      setEditor({ filename: doc.filename, content: 'Failed to load document content', isNew: false, isLoading: false });
    }
  }, []);

  const handleNew = useCallback(() => {
    setEditor({ filename: 'new-document.md', content: '', isNew: true, isLoading: false });
  }, []);

  const handleSave = useCallback(async (filename: string, content: string) => {
    const file = new File([content], filename, { type: 'text/plain' });
    await uploadDocument(filename, file);
    await fetchDocuments();
  }, [fetchDocuments]);

  // --- Send to chat handler ---

  const handleSendToChat = useCallback(async (doc: DocumentInfo) => {
    if (!onSendToChat) return;
    try {
      const content = await getDocumentContent(doc.filename);
      onSendToChat(`Here is the content of "${doc.filename}":\n\n${content}`);
    } catch {
      // Silently fail
    }
  }, [onSendToChat]);

  // --- File upload handlers ---

  const handleFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
      if (isValidFile(file.name)) {
        await upload(file);
      }
    }
  }, [upload]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    handleFiles(e.dataTransfer.files);
  }, [handleFiles]);

  const handleFileInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    handleFiles(e.target.files);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, [handleFiles]);

  // --- Render helpers ---

  const renderDocumentList = () => {
    if (documents.length === 0) {
      return (
        <EmptyState
          icon={<FileText className="h-12 w-12 opacity-50" />}
          title="No documents yet"
          description="Upload .txt or .md files to get started"
        />
      );
    }

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
            onEdit={() => handleEdit(doc)}
            onSendToChat={() => handleSendToChat(doc)}
            onRemove={() => remove(doc.filename)}
          />
        ))}
      </div>
    );
  };

  return (
    <div className="flex h-full flex-col">
      {error && <ErrorBanner message={error} onDismiss={clearError} />}

      <UploadArea
        isDragging={isDragging}
        isLoading={isLoading}
        fileInputRef={fileInputRef}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onFileInputChange={handleFileInputChange}
        onSelectFiles={() => fileInputRef.current?.click()}
        onNewDocument={handleNew}
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
              {searchQuery
                ? `Found ${filteredDocuments.length} of ${documents.length}`
                : `Your Documents (${documents.length})`}
            </h3>
            {renderDocumentList()}
          </div>
        </ScrollArea>
      </div>

      <DocumentPreviewDialog
        open={editor !== null}
        onOpenChange={(open) => !open && setEditor(null)}
        filename={editor?.filename ?? ''}
        content={editor?.content ?? null}
        isLoading={editor?.isLoading ?? false}
        editable
        onSave={handleSave}
      />
    </div>
  );
}

const DOCUMENT_TAB_KEY = 'snipscout-document-tab';

interface DocumentCanvasProps {
  onSendToChat?: (content: string) => void;
}

export function DocumentCanvas({ onSendToChat }: DocumentCanvasProps) {
  const [activeTab, setActiveTab] = useState(() => {
    return localStorage.getItem(DOCUMENT_TAB_KEY) ?? 'fetched';
  });

  const handleTabChange = (value: string) => {
    setActiveTab(value);
    localStorage.setItem(DOCUMENT_TAB_KEY, value);
  };

  return (
    <Tabs value={activeTab} onValueChange={handleTabChange} className="h-full gap-0">
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
        <ManageDocuments onSendToChat={onSendToChat} />
      </TabsContent>
    </Tabs>
  );
}
