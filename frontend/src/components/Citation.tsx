"use client";

import { FileTextIcon } from "lucide-react";
import type { HTMLAttributes } from "react";
import { useMemo, useState } from "react";
import { DocumentDialog } from "@/components/DocumentDialog";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  chunkOriginLabel,
  chunkPositionLabel,
  type FetchedChunk,
  isLinePosition,
  lineBounds,
  type LinePosition,
  parseLinePositions,
} from "@/lib/types";
import { chunksForDocument, useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

interface CitationProps extends HTMLAttributes<HTMLElement> {
  src?: string;
  line?: string;
  node?: unknown;
}

interface Evidence {
  chunk: FetchedChunk;
  lines: { number: number; text: string }[];
}

type OpenTarget = "document" | number | null;

function evidenceFromChunk(chunk: FetchedChunk, position: LinePosition): Evidence | null {
  const contentLines = chunk.content.split("\n");
  const chunkBounds: [number, number] | undefined = isLinePosition(chunk.position)
    ? lineBounds(chunk.position)
    : chunk.position.type === "full_document"
      ? [1, contentLines.length]
      : undefined;
  if (!chunkBounds) return null;

  const [start, end] = lineBounds(position);
  const [chunkStart, chunkEnd] = chunkBounds;
  if (start < chunkStart || end > chunkEnd) return null;

  const firstIndex = start - chunkStart;
  const selected = contentLines.slice(firstIndex, firstIndex + end - start + 1);
  if (selected.length !== end - start + 1) return null;

  return {
    chunk,
    lines: selected.map((text, index) => ({ number: start + index, text })),
  };
}

function evidenceForPosition(chunks: FetchedChunk[], position: LinePosition): Evidence[] {
  const unique = new Map<string, Evidence>();

  for (const chunk of chunks) {
    if (chunk.origin === "preview") continue;
    const evidence = evidenceFromChunk(chunk, position);
    if (!evidence) continue;
    const key = evidence.lines.map((line) => line.text).join("\n");
    if (!unique.has(key)) unique.set(key, evidence);
  }

  return [...unique.values()];
}

function EvidenceDialog({
  filename,
  position,
  evidence,
  onClose,
}: {
  filename: string;
  position: LinePosition;
  evidence: Evidence[];
  onClose: () => void;
}) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[80vh] max-w-3xl flex-col">
        <DialogHeader>
          <DialogTitle>{filename}</DialogTitle>
          <DialogDescription>
            {chunkPositionLabel(position)} from the tool output stored with this conversation. The
            document link opens the current workspace version separately.
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-4 pr-4">
            {evidence.map(({ chunk, lines }) => (
              <section key={chunk.id} className="overflow-hidden rounded-md border">
                <div className="border-b bg-muted px-3 py-2 text-xs text-muted-foreground">
                  Captured by {chunkOriginLabel(chunk)}
                </div>
                <pre className="overflow-x-auto p-3 text-sm">
                  {lines.map(({ number, text }) => (
                    <div key={number}>
                      <span className="select-none text-muted-foreground">{number}: </span>
                      {text}
                    </div>
                  ))}
                </pre>
              </section>
            ))}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}

export function Citation({ src, line }: CitationProps) {
  const [open, setOpen] = useState<OpenTarget>(null);
  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  // Subscribe to this document's entry rather than the whole map: its identity
  // changes only when the document itself gains a chunk, so a tool output for
  // some other file leaves every citation pointing at this one alone.
  const document = useFetchedDocumentsStore((state) =>
    src ? state.documents.get(src) : undefined,
  );

  const positions = useMemo(() => parseLinePositions(line), [line]);
  const documentChunks = useMemo(
    () => (document ? chunksForDocument(document, chunks) : []),
    [document, chunks],
  );
  const evidence = useMemo(
    () => positions.map((position) => evidenceForPosition(documentChunks, position)),
    [documentChunks, positions],
  );

  if (!src) return null;

  const displayName = src.split("/").pop() ?? src;

  return (
    <Badge
      variant="secondary"
      className="inline-flex flex-wrap items-center gap-1 rounded-full align-middle text-xs font-normal"
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className="flex cursor-pointer items-center gap-1 rounded-full hover:text-accent-foreground"
            onClick={() => setOpen("document")}
          >
            <FileTextIcon className="h-3 w-3 shrink-0" />
            {displayName}
          </button>
        </TooltipTrigger>
        <TooltipContent>Open the current document</TooltipContent>
      </Tooltip>
      {positions.length > 0 && <span aria-hidden className="h-3.5 w-px bg-border" />}
      {positions.map((position, index) => {
        const available = evidence[index].length > 0;

        return (
          <Tooltip key={index}>
            <TooltipTrigger asChild>
              <button
                type="button"
                disabled={!available}
                className="rounded-full bg-background/60 px-1.5 enabled:cursor-pointer enabled:hover:bg-accent disabled:text-muted-foreground"
                onClick={() => setOpen(index)}
              >
                {chunkPositionLabel(position)}
              </button>
            </TooltipTrigger>
            <TooltipContent>
              {available
                ? "Open the captured lines"
                : "No supporting tool output was captured for these lines."}
            </TooltipContent>
          </Tooltip>
        );
      })}
      {open === "document" && (
        <DocumentDialog open onOpenChange={(next) => !next && setOpen(null)} filename={src} />
      )}
      {typeof open === "number" && (
        <EvidenceDialog
          filename={src}
          position={positions[open]}
          evidence={evidence[open]}
          onClose={() => setOpen(null)}
        />
      )}
    </Badge>
  );
}
