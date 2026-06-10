"use client";

import { FileTextIcon } from "lucide-react";
import type { HTMLAttributes } from "react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import {
  chunkPositionLabel,
  type FetchedChunk,
  type LinePosition,
  makeChunkId,
  parseLinePositions,
} from "@/lib/types";
import { DocumentDialog } from "./DocumentDialog";

/**
 * Inline citation rendered by Streamdown for `<cite>` tags.
 *
 * A citation is a self-contained void marker: all data lives in attributes and
 * it has no children.  The `line` attribute accepts a single line, a
 * comma-separated list, or `start-end` ranges; the filename is shown once and
 * each resolved position becomes its own clickable chip that opens the document
 * at that span.
 */
interface CitationProps extends HTMLAttributes<HTMLElement> {
  src?: string;
  line?: string;
  node?: unknown;
}

/** Which target the dialog is open on: a position index, the full doc, or closed. */
type OpenTarget = number | "full" | null;

export function Citation({ src, line }: CitationProps) {
  const [open, setOpen] = useState<OpenTarget>(null);

  if (!src) return null;

  const displayName = src.split("/").pop() ?? src;
  const positions = parseLinePositions(line);

  const chunkFor = (position: LinePosition): FetchedChunk => {
    const source = "citation";
    return { id: makeChunkId(src, source, position), filename: src, content: "", source, position };
  };

  return (
    <Badge
      variant="secondary"
      className="inline-flex flex-wrap items-center gap-1 rounded-full align-middle text-xs font-normal"
    >
      <button
        type="button"
        className="flex cursor-pointer items-center gap-1 rounded-full hover:text-accent-foreground"
        onClick={() => setOpen("full")}
      >
        <FileTextIcon className="h-3 w-3 shrink-0" />
        {displayName}
      </button>
      {positions.length > 0 && <span aria-hidden className="h-3.5 w-px bg-border" />}
      {positions.map((position, index) => (
        <button
          key={index}
          type="button"
          className="cursor-pointer rounded-full bg-background/60 px-1.5 hover:bg-accent"
          onClick={() => setOpen(index)}
        >
          {chunkPositionLabel(position)}
        </button>
      ))}
      {open !== null && (
        <DocumentDialog
          open
          onOpenChange={(next) => {
            if (!next) setOpen(null);
          }}
          filename={src}
          chunk={open === "full" ? null : chunkFor(positions[open])}
          fallbackFilename={src}
          initialFullDoc={open === "full"}
          citationView
        />
      )}
    </Badge>
  );
}
