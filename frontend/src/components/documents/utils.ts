import type { FetchedChunk } from "../../lib/types";

/** A normalized read span (fractions in 0..1) within a document. */
export interface MapSegment {
  start: number;
  end: number;
}

/**
 * Normalized spans marking where a document's chunks sit within it, used to
 * render the coverage bar.  Line numbers are the common unit (every located
 * chunk carries them); the denominator is the document's known length, falling
 * back to the furthest read line when the full content has not been fetched.
 * Returns an empty array for documents with no locatable chunks (web/text).
 */
export function documentReadMap(chunks: FetchedChunk[], fullContent?: string): MapSegment[] {
  const ranges: Array<[number, number]> = [];

  for (const chunk of chunks) {
    const pos = chunk.position;

    if (pos.type === "full_document") return [{ start: 0, end: 1 }];

    if (pos.type === "line") ranges.push([pos.line, pos.line]);
    else if (pos.type === "line_range") ranges.push([pos.startLine, pos.endLine]);
  }

  if (ranges.length === 0) return [];

  const knownLines = fullContent ? fullContent.split("\n").length : 0;
  const total = Math.max(knownLines, ...ranges.map(([, end]) => end));

  if (total <= 0) return [];

  const clamp = (value: number): number => Math.min(1, Math.max(0, value));
  const sorted = ranges
    .map(([start, end]): MapSegment => ({ start: clamp((start - 1) / total), end: clamp(end / total) }))
    .sort((a, b) => a.start - b.start);

  // Merge overlapping or touching spans so dense reads render as one block.
  const merged: MapSegment[] = [];

  for (const seg of sorted) {
    const last = merged.at(-1);

    if (last && seg.start <= last.end) last.end = Math.max(last.end, seg.end);
    else merged.push({ ...seg });
  }

  return merged;
}

/** Human-friendly relative date label (Today, Yesterday, N days ago, ...). */
export function formatRelativeDate(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays} days ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
  return date.toLocaleDateString();
}
