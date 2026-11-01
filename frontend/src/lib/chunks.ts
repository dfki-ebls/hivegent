import { i18n } from "@/i18n";

/** Position of a chunk within its parent document (discriminated union). */
export type ChunkPosition =
  | { type: "line"; line: number }
  | { type: "line_range"; startLine: number; endLine: number }
  | { type: "full_document" }
  | { type: "web_result"; url: string }
  /** Unlocated span — resolve by searching `FetchedChunk.content` in the document. */
  | { type: "text" };

/** The model tool a fetched chunk came from. */
export type ChunkOrigin = "read" | "grep" | "search" | "web";

/** A single fetched chunk from a persisted tool result. */
export interface FetchedChunk {
  id: string;
  filename: string;
  content: string;
  /** Where the chunk came from; drives the origin badge. */
  origin: ChunkOrigin;
  /** Optional query/pattern for display (grep pattern, search/web query). */
  detail?: string;
  /** Tool call that captured this evidence, keeping repeated reads distinct. */
  sourceId?: string;
  position: ChunkPosition;
  /**
   * Exact character offsets of the chunk in the original document.
   * Populated for semantic-search chunks so the canvas can highlight
   * the precise span rather than rounding to whole lines.  Never sent
   * to the LLM — citations rely on {@link ChunkPosition} line numbers.
   */
  startIndex?: number;
  endIndex?: number;
}

/** An image binary attached to the model via the read_binary_document tool. */
export interface FetchedImage {
  /** Canonical workspace path of the image, used to fetch the blob. */
  filePath: string;
  mediaType: string;
}

/** A document that groups one or more fetched chunks. */
export interface FetchedDocument {
  filename: string;
  fullContent?: string;
  /**
   * Total line count of the document as the latest read reported it.  The
   * coverage map prefers it over the indexed count, which may predate an edit.
   */
  totalLines?: number;
  chunkIds: string[];
  /**
   * Set when the model read an image whose description lives at this
   * document's path (`<stem>.md`); merges the image with its caption.
   */
  image?: FetchedImage;
}

/** Build a deterministic chunk ID from its attributes. */
export function makeChunkId(
  filename: string,
  origin: ChunkOrigin,
  detail: string | undefined,
  position: ChunkPosition,
  sourceId?: string,
): string {
  let positionKey: string;
  switch (position.type) {
    case "line":
      positionKey = `line_${position.line}`;
      break;
    case "line_range":
      positionKey = `lines_${position.startLine}_${position.endLine}`;
      break;
    case "full_document":
      positionKey = "full";
      break;
    case "web_result":
      positionKey = "web";
      break;
    case "text":
      positionKey = "text";
      break;
  }

  return `${filename}::${detail ? `${origin}:${detail}` : origin}::${positionKey}${
    sourceId ? `::${sourceId}` : ""
  }`;
}

/**
 * Numeric sort key for a chunk position.
 * Full document sorts first (-1), then by chunk index / line number.
 */
export function chunkSortKey(position: ChunkPosition): number {
  switch (position.type) {
    case "full_document":
      return -1;
    case "web_result":
      return 0;
    case "line":
      return position.line;
    case "line_range":
      return position.startLine;
    case "text":
      return Number.MAX_SAFE_INTEGER;
  }
}

/** Human-readable label for a chunk position. */
export function chunkPositionLabel(position: ChunkPosition): string {
  switch (position.type) {
    case "line":
      return i18n.t(($) => $.options.chunkPosition.line, { line: position.line });
    case "line_range":
      return i18n.t(($) => $.options.chunkPosition.lineRange, {
        start: position.startLine,
        end: position.endLine,
      });
    case "full_document":
      return i18n.t(($) => $.options.chunkPosition.fullDocument);
    case "web_result": {
      try {
        return new URL(position.url).hostname;
      } catch {
        return position.url;
      }
    }
    case "text":
      return i18n.t(($) => $.options.chunkPosition.citedText);
  }
}

/** Localized label for how a chunk was captured ("Grep: foo", "Read"). */
export function chunkOriginLabel({
  origin,
  detail,
}: Pick<FetchedChunk, "origin" | "detail">): string {
  const label = i18n.t(($) => $.documents.chunkOrigin[origin]);

  return detail ? i18n.t(($) => $.documents.chunkOriginDetail, { origin: label, detail }) : label;
}

/** The line-based `ChunkPosition` variants a citation `line` attribute yields. */
export type LinePosition = Extract<ChunkPosition, { type: "line" | "line_range" }>;

/**
 * Whether a position carries concrete line numbers, the single unit the
 * coverage map can place.
 */
export function isLinePosition(position: ChunkPosition): position is LinePosition {
  return position.type === "line" || position.type === "line_range";
}

/** The inclusive `[start, end]` line span a line position covers. */
export function lineBounds(position: LinePosition): [number, number] {
  return position.type === "line"
    ? [position.line, position.line]
    : [position.startLine, position.endLine];
}

/**
 * Parse a citation `line` attribute ("42", "42,46", "50-55", "42,50-55,90")
 * into canonical line positions, skipping malformed tokens.
 */
export function parseLinePositions(line: string | undefined): LinePosition[] {
  if (!line) return [];
  const positions: LinePosition[] = [];
  for (const token of line.split(",")) {
    const match = /^\s*(\d+)(?:\s*-\s*(\d+))?\s*$/.exec(token);
    if (!match) continue;
    const startLine = Number(match[1]);
    const endLine = Number(match[2] ?? match[1]);
    if (startLine < 1 || endLine < startLine) continue;
    positions.push(
      match[2] ? { type: "line_range", startLine, endLine } : { type: "line", line: startLine },
    );
  }
  return positions;
}

/** Sort chunks by position (full document first, then ascending). */
export function sortChunks(chunks: FetchedChunk[]): FetchedChunk[] {
  return [...chunks].sort((a, b) => chunkSortKey(a.position) - chunkSortKey(b.position));
}
