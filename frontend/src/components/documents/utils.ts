import { type FetchedChunk, isLinePosition, lineBounds } from "@/lib/types";

/** A normalized read span (fractions in 0..1) within a document. */
interface MapSegment {
  start: number;
  end: number;
}

/** The inclusive line spans a document's chunks cover, or `"whole"` once it was read in full. */
export type ReadSpans = Array<[number, number]> | "whole";

export function readSpans(chunks: FetchedChunk[]): ReadSpans {
  const spans: Array<[number, number]> = [];

  for (const { position } of chunks) {
    if (position.type === "full_document") return "whole";

    if (isLinePosition(position)) {
      spans.push(lineBounds(position));
    }
  }

  return spans;
}

/**
 * Normalized segments marking where *spans* sit within a document of
 * *totalLines*, used to render the coverage bar.  Returns an empty array, so
 * the bar is hidden rather than guessed, whenever there are no spans or the
 * document length is unknown.
 */
export function documentReadMap(spans: ReadSpans, totalLines?: number | null): MapSegment[] {
  if (spans === "whole") return [{ start: 0, end: 1 }];

  if (spans.length === 0 || totalLines == null) return [];

  const total = Math.max(totalLines, ...spans.map(([, end]) => end));

  if (total <= 0) return [];

  const clamp = (value: number): number => Math.min(1, Math.max(0, value));

  // Normalize each span against the document length, merging overlapping or
  // touching spans (sorted by start) so dense reads render as one block.
  const merged: MapSegment[] = [];

  for (const [start, end] of spans.toSorted((a, b) => a[0] - b[0])) {
    const seg: MapSegment = { start: clamp((start - 1) / total), end: clamp(end / total) };
    const last = merged.at(-1);

    if (last && seg.start <= last.end) last.end = Math.max(last.end, seg.end);
    else merged.push(seg);
  }

  return merged;
}
