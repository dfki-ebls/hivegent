import { useQuery } from "@tanstack/react-query";

import type { FetchedChunk } from "@/lib/types";
import { documentQueries } from "@/queries/documents";
import { cn } from "@/lib/utils";
import { documentReadMap, readSpans } from "@/components/documents/utils";

interface DocumentMapProps {
  filename: string;
  chunks: FetchedChunk[];
  /** The length a read reported, which wins over the indexed one. */
  totalLines?: number;
  className?: string;
}

/** Coverage bar showing where a document's read chunks sit within the whole. */
export function DocumentMap({ filename, chunks, totalLines, className }: DocumentMapProps) {
  const spans = readSpans(chunks);
  // Only partial spans need a length, so a document without any never asks.
  const needsLength = totalLines === undefined && spans !== "whole" && spans.length > 0;
  const { data: indexedLines } = useQuery({
    ...documentQueries.lineCount(filename),
    enabled: needsLength,
  });
  const segments = documentReadMap(spans, totalLines ?? indexedLines);

  if (segments.length === 0) return null;

  return (
    <div
      aria-hidden
      className={cn(
        "relative ml-auto h-1.5 w-2/5 max-w-40 shrink-0 overflow-hidden rounded-full bg-muted",
        className,
      )}
    >
      {segments.map((seg, i) => (
        <div
          key={i}
          className="absolute inset-y-0 rounded-full bg-primary"
          style={{
            left: `${seg.start * 100}%`,
            width: `${Math.max((seg.end - seg.start) * 100, 2)}%`,
          }}
        />
      ))}
    </div>
  );
}
