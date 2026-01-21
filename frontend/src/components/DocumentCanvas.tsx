import { useState } from 'react';
import { FileText, FolderOpen, Search, Upload } from 'lucide-react';

import type { StoredDocument } from '../lib/types';
import { useDocumentStore } from '../stores/document-store';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { ScrollArea } from './ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';

function FetchedDocuments() {
  const documents = useDocumentStore((state) => state.documents);
  const [selectedDocument, setSelectedDocument] =
    useState<StoredDocument | null>(null);

  const documentList = Array.from(documents.values()).sort(
    (a, b) => (b.score ?? 0) - (a.score ?? 0)
  );

  if (documentList.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-muted-foreground">
        <Search className="h-12 w-12 opacity-50" />
        <p className="text-center">Fetched documents will appear here</p>
        <p className="text-center text-sm">
          Ask questions in the chat to search and fetch documents
        </p>
      </div>
    );
  }

  return (
    <>
      <ScrollArea className="h-full">
        <div className="grid gap-3 p-4 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
          {documentList.map((doc) => (
            <Card
              key={doc.filename}
              className="flex cursor-pointer flex-col transition-colors hover:bg-muted/50"
              onClick={() => setSelectedDocument(doc)}
            >
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="line-clamp-1 text-sm">
                    {doc.filename}
                  </CardTitle>
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
          ))}
        </div>
      </ScrollArea>

      <Dialog
        open={selectedDocument !== null}
        onOpenChange={(open) => !open && setSelectedDocument(null)}
      >
        <DialogContent className="max-h-[80vh] max-w-3xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {selectedDocument?.filename}
              {selectedDocument?.score !== undefined && (
                <Badge variant="secondary">
                  {(selectedDocument.score * 100).toFixed(1)}%
                </Badge>
              )}
            </DialogTitle>
            {selectedDocument && (
              <div className="flex flex-wrap gap-1">
                {selectedDocument.sources.map((source) => (
                  <Badge key={source} variant="outline" className="text-xs">
                    {source}
                  </Badge>
                ))}
              </div>
            )}
          </DialogHeader>
          <ScrollArea className="max-h-[60vh]">
            <pre className="whitespace-pre-wrap text-sm">
              {selectedDocument?.content}
            </pre>
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ManageDocuments() {
  // Placeholder documents for UI preview
  const placeholderDocs = [
    { id: '1', name: 'meeting-notes-2024.md', size: '12 KB', date: '2 days ago' },
    { id: '2', name: 'project-requirements.pdf', size: '245 KB', date: '1 week ago' },
    { id: '3', name: 'research-summary.txt', size: '8 KB', date: '2 weeks ago' },
  ];

  return (
    <div className="flex h-full flex-col">
      {/* Upload area */}
      <div className="border-b p-4">
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-muted-foreground/25 bg-muted/25 p-6 transition-colors hover:border-muted-foreground/50 hover:bg-muted/50">
          <Upload className="h-10 w-10 text-muted-foreground" />
          <div className="text-center">
            <p className="font-medium">Drop files here to upload</p>
            <p className="text-sm text-muted-foreground">
              or click to browse
            </p>
          </div>
          <Button variant="secondary" size="sm" disabled>
            Select Files
          </Button>
        </div>
      </div>

      {/* Document list */}
      <ScrollArea className="flex-1">
        <div className="p-4">
          <h3 className="mb-3 text-sm font-medium text-muted-foreground">
            Your Documents
          </h3>
          <div className="space-y-2">
            {placeholderDocs.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50"
              >
                <FileText className="h-8 w-8 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-sm">{doc.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {doc.size} · {doc.date}
                  </p>
                </div>
                <Button variant="ghost" size="sm" disabled>
                  Remove
                </Button>
              </div>
            ))}
          </div>
        </div>
      </ScrollArea>

      {/* Empty state hint */}
      <div className="border-t p-4">
        <p className="text-center text-xs text-muted-foreground">
          Document management coming soon
        </p>
      </div>
    </div>
  );
}

export function DocumentCanvas() {
  return (
    <Tabs defaultValue="fetched" className="h-full gap-0">
      <div className="shrink-0 border-b px-4 flex items-center h-[60px]">
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
        <ManageDocuments />
      </TabsContent>
    </Tabs>
  );
}
