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
 * Extends `HTMLAttributes<HTMLElement>` so it satisfies Streamdown's
 * `Components` mapped type for the intrinsic `cite` element.  The
 * custom `filename` and `line` attributes come from `allowedTags`.
 */
interface CitationProps extends HTMLAttributes<HTMLElement> {
  filename?: string;
  line?: string;
  node?: unknown;
}

export function Citation({ filename, line, children }: CitationProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const documents = useFetchedDocumentsStore((state) => state.documents);

  const lineNumber = useMemo(() => {
    if (line === undefined) return null;
    const n = parseInt(line, 10);
    return Number.isNaN(n) ? null : n;
  }, [line]);

  // Synthesize an ephemeral chunk anchored at the cited line.  The
  // dialog resolves it to a position in the freshly-fetched full
  // document; no lookup against fetched chunks — the LLM cites by
  // line number alone.
  const anchorChunk = useMemo((): FetchedChunk | null => {
    if (!filename || lineNumber === null) return null;
    return {
      id: `citation:${filename}:L${lineNumber}`,
      filename,
      content: "",
      source: `line ${lineNumber}`,
      position: { type: "line", line: lineNumber },
    };
  }, [filename, lineNumber]);

  // If the full document is already cached, use it to render a hover
  // preview of the cited line.
  const previewText = useMemo(() => {
    if (!filename || lineNumber === null) return null;
    const doc = documents.get(filename);
    if (!doc?.fullContent) return null;
    const lines = doc.fullContent.split("\n");
    if (lineNumber < 1 || lineNumber > lines.length) return null;
    return lines[lineNumber - 1];
  }, [filename, lineNumber, documents]);

  if (!filename) {
    return <span>{children}</span>;
  }

  const displayName = filename.split("/").pop() ?? filename;
  const positionLabel = lineNumber !== null ? `L${lineNumber}` : undefined;

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
            {positionLabel && ` ${positionLabel}`}
          </Badge>
        </HoverCardTrigger>
        <HoverCardContent className="w-80 p-4" side="top">
          <div className="space-y-2">
            <h4 className="truncate font-medium text-sm">{filename}</h4>
            {positionLabel && (
              <p className="text-xs text-muted-foreground">{positionLabel}</p>
            )}
            {previewText ? (
              <blockquote className="border-l-2 border-muted pl-3 text-sm text-muted-foreground italic line-clamp-4">
                {previewText}
              </blockquote>
            ) : (
              <p className="text-xs text-muted-foreground italic">
                Click to load and view in context
              </p>
            )}
          </div>
        </HoverCardContent>
      </HoverCard>
      <DocumentDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        filename={filename}
        chunk={anchorChunk}
        fallbackFilename={filename}
        initialFullDoc={lineNumber === null}
      />
    </span>
  );
}
