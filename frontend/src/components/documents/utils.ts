import { type FetchedChunk, isLinePosition } from "@/lib/types";

/** A normalized read span (fractions in 0..1) within a document. */
export interface MapSegment {
  start: number;
  end: number;
}

/**
 * Normalized spans marking where a document's chunks sit within it, used to
 * render the coverage bar.  Line numbers are the common unit (every located
 * chunk carries them) and *totalLines* (recorded once from a partial read or
 * the fetched full content) is the denominator.  Returns an empty array, so
 * the bar is hidden rather than guessed, whenever there are no locatable
 * chunks or the document length is still unknown.
 */
export function documentReadMap(chunks: FetchedChunk[], totalLines?: number): MapSegment[] {
  const ranges: Array<[number, number]> = [];

  for (const { position } of chunks) {
    if (position.type === "full_document") return [{ start: 0, end: 1 }];

    if (isLinePosition(position)) {
      ranges.push(
        position.type === "line"
          ? [position.line, position.line]
          : [position.startLine, position.endLine],
      );
    }
  }

  if (ranges.length === 0 || totalLines === undefined) return [];

  const total = Math.max(totalLines, ...ranges.map(([, end]) => end));

  if (total <= 0) return [];

  const clamp = (value: number): number => Math.min(1, Math.max(0, value));

  // Normalize each span against the document length, merging overlapping or
  // touching spans (sorted by start) so dense reads render as one block.
  const merged: MapSegment[] = [];

  for (const [start, end] of ranges.sort((a, b) => a[0] - b[0])) {
    const seg: MapSegment = { start: clamp((start - 1) / total), end: clamp(end / total) };
    const last = merged.at(-1);

    if (last && seg.start <= last.end) last.end = Math.max(last.end, seg.end);
    else merged.push(seg);
  }

  return merged;
}

const relativeTime = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/** Human-friendly relative date label (today, yesterday, N days ago, ...). */
export function formatRelativeDate(dateString: string): string {
  const date = new Date(dateString);
  const diffDays = Math.floor((Date.now() - date.getTime()) / 86_400_000);

  if (diffDays < 7) return relativeTime.format(-diffDays, "day");
  if (diffDays < 30) return relativeTime.format(-Math.floor(diffDays / 7), "week");
  return date.toLocaleDateString();
}
