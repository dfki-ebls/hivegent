"use client";

import { Badge } from "@/components/ui/badge";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { FileTextIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { DocumentPreviewDialog } from "./DocumentPreviewDialog";

/**
 * Inline citation rendered by Streamdown for `<cite>` tags.
 *
 * Displays the cited text followed by a filename badge.
 * Hover shows a preview card, click opens the full document modal.
 *
 * Accepts `Record<string, unknown>` because Streamdown's `Components` type
 * maps `cite` (a known HTML element) to its intrinsic props, while the actual
 * attributes (`filename`, `chunk`) come from the custom `allowedTags` config.
 */
export function Citation(props: Record<string, unknown>) {
  const filename = props.filename as string | undefined;
  const chunk = props.chunk as string | undefined;
  const children = props.children as ReactNode;
  const [dialogOpen, setDialogOpen] = useState(false);
  const doc = useFetchedDocumentsStore((state) =>
    filename ? state.documents.get(filename) : undefined
  );

  if (!filename) {
    return <span>{children}</span>;
  }

  const displayName = filename.split("/").pop() ?? filename;
  const chunkIndex = chunk ? parseInt(chunk, 10) : undefined;
  const previewText = doc?.content
    ? doc.content.slice(0, 300)
    : null;

  return (
    <span className="inline">
      <span className="transition-colors hover:bg-accent/50 rounded-sm">
        {children}
      </span>
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
              <p className="text-xs text-muted-foreground">
                Chunk {chunkIndex}
              </p>
            )}
            {previewText ? (
              <blockquote className="border-l-2 border-muted pl-3 text-sm text-muted-foreground italic line-clamp-4">
                {previewText}
                {(doc?.content.length ?? 0) > 300 && "\u2026"}
              </blockquote>
            ) : (
              <p className="text-xs text-muted-foreground italic">
                No preview available
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              Click to view full document
            </p>
          </div>
        </HoverCardContent>
      </HoverCard>
      <DocumentPreviewDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        filename={filename}
        content={doc?.content ?? null}
        isLoading={dialogOpen && !doc}
      />
    </span>
  );
}
