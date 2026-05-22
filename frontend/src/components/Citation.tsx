"use client";

import { FileTextIcon } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { type ChunkPosition, type FetchedChunk, makeChunkId } from "@/lib/types";
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

  if (!filename) {
    return <span>{children}</span>;
  }

  const displayName = filename.split("/").pop() ?? filename;
  const positionLabel = lineNumber !== null ? `L${lineNumber}` : undefined;

  return (
    <span className="inline">
      {children}
      <Badge
        variant="secondary"
        className="ml-0.5 cursor-pointer rounded-full align-middle text-xs hover:bg-accent"
        onClick={() => setDialogOpen(true)}
      >
        <FileTextIcon className="mr-1 h-3 w-3" />
        {displayName}
        {positionLabel && ` ${positionLabel}`}
      </Badge>
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
