import { VOID_TAG_RE } from "@/lib/normalize-void-tags";
import {
  type FetchedChunk,
  isLinePosition,
  lineBounds,
  type LinePosition,
  parseLinePositions,
} from "@/lib/types";

const ATTRIBUTE_RE = /([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;

/** The attributes of a `<cite>` tag, which `Citation` renders. */
export interface CiteTag {
  src?: string;
  line?: string;
}

/** The `<cite>` tags of *text* in order. */
export function parseCitations(text: string): CiteTag[] {
  return Array.from(text.matchAll(VOID_TAG_RE))
    .filter(([, tag]) => tag?.toLowerCase() === "cite")
    .map(([, , attributes = ""]) => {
      const values = new Map(
        Array.from(attributes.matchAll(ATTRIBUTE_RE), ([, name, double, single]) => [
          name.toLowerCase(),
          double ?? single,
        ]),
      );

      return { src: values.get("src"), line: values.get("line") };
    });
}

/** The lines a citation position names, as one captured chunk shows them. */
export interface Evidence {
  chunk: FetchedChunk;
  startLine: number;
  text: string;
}

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

  return { chunk, startLine: start, text: selected.join("\n") };
}

/** The distinct texts the captured *chunks* show at *position*, none when no capture covers it. */
function evidenceForPosition(chunks: FetchedChunk[], position: LinePosition): Evidence[] {
  const unique = new Map<string, Evidence>();

  for (const chunk of chunks) {
    const evidence = evidenceFromChunk(chunk, position);
    if (!evidence) continue;
    if (!unique.has(evidence.text)) unique.set(evidence.text, evidence);
  }

  return [...unique.values()];
}

/** A position a citation names, with the evidence the captured chunks show for it. */
export interface ResolvedPosition {
  position: LinePosition;
  evidence: Evidence[];
}

/** The positions of a citation's *line* in a document, resolved against its captured *chunks*. */
export function resolveCitation(chunks: FetchedChunk[], line: string | undefined): ResolvedPosition[] {
  return parseLinePositions(line).map((position) => ({
    position,
    evidence: evidenceForPosition(chunks, position),
  }));
}
