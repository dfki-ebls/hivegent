"use client";

import { FileTextIcon } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import type { FetchedChunk } from "@/lib/types";
import { sortChunks } from "@/lib/types";
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

/** Extract plain text from React children for content matching. */
function childrenToText(node: ReactNode): string {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(childrenToText).join("");
  return "";
}

export function Citation({ filename, chunk, children }: CitationProps) {
  const [dialogOpen, setDialogOpen] = useState(false);

  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);

  const citedText = useMemo(() => childrenToText(children), [children]);

  // Find the best matching chunk for this citation
  const matchedChunk = useMemo((): FetchedChunk | null => {
    if (!filename) return null;
    const doc = documents.get(filename);
    if (!doc) return null;

    const siblings = doc.chunkIds
      .map((id) => chunks.get(id))
      .filter((c): c is FetchedChunk => c != null);
    if (siblings.length === 0) return null;

    const chunkIndex = chunk !== undefined ? parseInt(chunk, 10) : undefined;

    // 1. Exact match by chunk_index position
    if (chunkIndex !== undefined && !Number.isNaN(chunkIndex)) {
      const exact = siblings.find(
        (c) => c.position.type === "chunk_index" && c.position.chunkIndex === chunkIndex,
      );
      if (exact) return exact;
    }

    // 2. Content match: find the chunk that contains the cited text
    if (citedText.length > 0) {
      const contentMatch = siblings.find((c) => c.content.includes(citedText));
      if (contentMatch) return contentMatch;
    }

    // 3. Ordinal fallback when a chunk index was specified
    if (chunkIndex !== undefined && !Number.isNaN(chunkIndex)) {
      const sorted = sortChunks(siblings);
      if (chunkIndex < sorted.length) return sorted[chunkIndex];
    }

    // 4. Last resort: first sibling as sidebar anchor
    return siblings[0];
  }, [filename, chunk, citedText, documents, chunks]);

  // Document-level citations (no chunk attr and no content match) open full-doc
  const openFullDoc = chunk === undefined && matchedChunk?.position.type !== "chunk_index";

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
        initialFullDoc={openFullDoc}
      />
    </span>
  );
}
