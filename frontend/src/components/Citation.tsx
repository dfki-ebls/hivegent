"use client";

import { FileTextIcon } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { type ChunkPosition, type FetchedChunk, makeChunkId } from "@/lib/types";
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

function extractText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (typeof node === "object" && "props" in node) {
    return extractText((node.props as { children?: ReactNode }).children);
  }
  return "";
}

export function Citation({ filename, line, children }: CitationProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const doc = useFetchedDocumentsStore((state) =>
    filename ? state.documents.get(filename) : undefined,
  );

  const lineNumber = useMemo(() => {
    if (line === undefined) return null;
    const n = parseInt(line, 10);
    return Number.isFinite(n) && n >= 1 ? n : null;
  }, [line]);

  const citedText = useMemo(() => extractText(children).trim(), [children]);

  const anchorChunk = useMemo((): FetchedChunk | null => {
    if (!filename) return null;
    if (lineNumber === null && !citedText) return null;
    const position: ChunkPosition =
      lineNumber !== null ? { type: "line", line: lineNumber } : { type: "text" };
    const source = lineNumber !== null ? `line ${lineNumber}` : "cited text";
    return {
      id: makeChunkId(filename, source, position),
      filename,
      content: citedText,
      source,
      position,
    };
  }, [filename, lineNumber, citedText]);

  const previewText = useMemo(() => {
    if (lineNumber !== null) {
      const lines = doc?.fullContent?.split("\n");
      if (lines && lineNumber <= lines.length) return lines[lineNumber - 1];
    }
    return citedText || null;
  }, [lineNumber, citedText, doc]);

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
            {positionLabel && <p className="text-xs text-muted-foreground">{positionLabel}</p>}
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
        initialFullDoc={anchorChunk === null}
      />
    </span>
  );
}
