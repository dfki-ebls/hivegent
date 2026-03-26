"use client";

import { FileTextIcon } from "lucide-react";
import type { HTMLAttributes } from "react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import type { FetchedChunk } from "@/lib/types";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { DocumentDialog } from "./DocumentDialog";

/**
 * Inline citation rendered by Streamdown for `<cite>` tags.
 *
 * Displays the cited text followed by a filename badge.
 * Hover shows a preview card, click opens the DocumentDialog.
 *
 * Extends `HTMLAttributes<HTMLElement>` so it satisfies Streamdown's
 * `Components` mapped type for the intrinsic `cite` element.  The custom
 * `filename` and `chunk` attributes come from the `allowedTags` config.
 */
interface CitationProps extends HTMLAttributes<HTMLElement> {
  filename?: string;
  chunk?: string;
  node?: unknown;
}

export function Citation({ filename, chunk, children }: CitationProps) {
  const [dialogOpen, setDialogOpen] = useState(false);

  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);

  // Find the best matching chunk for this citation
  const matchedChunk = useMemo((): FetchedChunk | null => {
    if (!filename) return null;
    const doc = documents.get(filename);
    if (!doc) return null;

    const chunkIndex = chunk !== undefined ? parseInt(chunk, 10) : undefined;

    // Try to find a chunk matching by chunk_index
    if (chunkIndex !== undefined) {
      for (const id of doc.chunkIds) {
        const c = chunks.get(id);
        if (c && c.position.type === "chunk_index" && c.position.chunkIndex === chunkIndex) {
          return c;
        }
      }
    }

    // Fallback: return the first chunk for this document
    for (const id of doc.chunkIds) {
      const c = chunks.get(id);
      if (c) return c;
    }
    return null;
  }, [filename, chunk, documents, chunks]);

  if (!filename) {
    return <span>{children}</span>;
  }

  const displayName = filename.split("/").pop() ?? filename;
  const chunkIndex = chunk !== undefined ? parseInt(chunk, 10) : undefined;
  const previewText = matchedChunk?.content.slice(0, 300) ?? null;

  return (
    <span className="inline">
      <span className="transition-colors hover:bg-accent/50 rounded-sm">{children}</span>
      <HoverCard openDelay={200} closeDelay={100}>
        <HoverCardTrigger asChild>
          <Badge
            variant="secondary"
            className="ml-0.5 cursor-pointer rounded-full align-middle text-xs hover:bg-accent"
            onClick={() => setDialogOpen(true)}
          >
            <FileTextIcon className="mr-1 h-3 w-3" />
            {displayName}
            {chunkIndex !== undefined && ` #${chunkIndex}`}
          </Badge>
        </HoverCardTrigger>
        <HoverCardContent className="w-80 p-4" side="top">
          <div className="space-y-2">
            <h4 className="truncate font-medium text-sm">{filename}</h4>
            {chunkIndex !== undefined && (
              <p className="text-xs text-muted-foreground">Chunk {chunkIndex}</p>
            )}
            {previewText ? (
              <blockquote className="border-l-2 border-muted pl-3 text-sm text-muted-foreground italic line-clamp-4">
                {previewText}
                {(matchedChunk?.content.length ?? 0) > 300 && "\u2026"}
              </blockquote>
            ) : (
              <p className="text-xs text-muted-foreground italic">No preview available</p>
            )}
            <p className="text-xs text-muted-foreground">Click to view in context</p>
          </div>
        </HoverCardContent>
      </HoverCard>
      <DocumentDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        filename={filename}
        chunk={matchedChunk}
        fallbackFilename={filename}
      />
    </span>
  );
}
