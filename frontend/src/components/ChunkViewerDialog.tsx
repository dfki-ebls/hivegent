import { Loader2, RefreshCw, Scissors } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { getDocumentChunks } from "../lib/api";
import type { ChunkedDocumentResponse } from "../lib/types";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { ScrollArea } from "./ui/scroll-area";

interface ChunkViewerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filename: string;
  onRechunk?: () => Promise<void>;
}

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleString();
}

export function ChunkViewerDialog({
  open,
  onOpenChange,
  filename,
  onRechunk,
}: ChunkViewerDialogProps) {
  const [data, setData] = useState<ChunkedDocumentResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isRechunking, setIsRechunking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchChunks = useCallback(() => {
    if (!filename) return;
    setIsLoading(true);
    setError(null);
    setData(null);

    getDocumentChunks(filename)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setIsLoading(false));
  }, [filename]);

  useEffect(() => {
    if (!open || !filename) return;
    fetchChunks();
  }, [open, filename, fetchChunks]);

  const handleRechunk = useCallback(async () => {
    if (!onRechunk) return;
    setIsRechunking(true);
    try {
      await onRechunk();
      fetchChunks();
    } finally {
      setIsRechunking(false);
    }
  }, [onRechunk, fetchChunks]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="h-[90vh] w-[90vw] max-w-4xl! flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Scissors className="h-5 w-5" />
            Chunks: {filename}
          </DialogTitle>
          {data && (
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <Badge variant="secondary">
                Chunking: {data.chunking_pipeline}
              </Badge>
              <Badge variant="secondary">
                Chunk size: {data.chunk_size} tokens
              </Badge>
              <Badge variant="secondary">{data.chunk_count} chunks</Badge>
              <Badge variant="outline">
                Created: {formatDate(data.created_at)}
              </Badge>
              {onRechunk && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRechunk}
                  disabled={isRechunking}
                >
                  <RefreshCw
                    className={`h-3 w-3 mr-1 ${isRechunking ? "animate-spin" : ""}`}
                  />
                  Rechunk
                </Button>
              )}
            </div>
          )}
        </DialogHeader>

        {isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="flex flex-1 items-center justify-center">
            <p className="text-muted-foreground">{error}</p>
          </div>
        ) : data ? (
          <ScrollArea className="flex-1 min-h-0">
            <div className="space-y-3 p-4">
              {data.chunks.map((chunk) => (
                <Card key={chunk.index}>
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-sm">
                        Chunk {chunk.index}
                      </CardTitle>
                      <div className="flex gap-2">
                        <Badge variant="secondary">
                          {chunk.token_count} tokens
                        </Badge>
                        <Badge variant="outline">
                          chars {chunk.start_index}–{chunk.end_index}
                        </Badge>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <pre className="whitespace-pre-wrap text-xs text-muted-foreground font-mono">
                      {chunk.text}
                    </pre>
                  </CardContent>
                </Card>
              ))}
            </div>
          </ScrollArea>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
