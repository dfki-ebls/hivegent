import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useConversationId } from "@/components/chat/chat-context";
import {
  canonicalPath,
  isWorkspacePath,
  splitScopePath,
  documentQueries,
  type ChunkedDocumentResponse,
} from "@/api/documents";
import {
  type FetchedChunk,
  type FetchedImage,
  chunkOriginLabel,
  chunkPositionLabel,
  isLinePosition,
  lineBounds,
  sortChunks,
} from "@/lib/chunks";
import { errorMessage, isWebUrl } from "@/lib/utils";
import { type DocumentPreview, previewKind } from "@/lib/original-preview";
import { type ContentChunk, type ContentModel } from "@/components/documents/dialog/model";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { chunksForDocument, useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

/** The index each line of *content* starts at, so line positions resolve without splitting it per chunk. */
function lineStarts(content: string): number[] {
  const starts = [0];

  for (let i = content.indexOf("\n"); i !== -1; i = content.indexOf("\n", i + 1)) {
    starts.push(i + 1);
  }

  return starts;
}

/** The preview of the original behind the entry at *documentPath*, if the browser can render it. */
function entryPreview(
  documentPath: string,
  metadata: ChunkedDocumentResponse | null,
): DocumentPreview | null {
  const originalPath = metadata?.original_path;
  const kind = originalPath ? previewKind(metadata.mime, originalPath) : null;

  if (!originalPath || !kind) return null;

  return kind === "media"
    ? {
        kind,
        path: canonicalPath(splitScopePath(documentPath).scope, originalPath),
        mediaType: metadata.mime,
        alt: documentPath,
      }
    : { kind, documentPath };
}

type RangeResolver = (chunk: FetchedChunk) => { start: number; end: number } | null;

/**
 * Resolve chunk character ranges within ``fullContent``, indexing its lines once for every chunk.
 *
 * Prefers exact character offsets from the backend (semantic-search
 * chunks carry these) so highlights don't round up to whole lines.
 * Falls back to line numbers, then to a text search.
 */
function chunkRangeResolver(fullContent: string): RangeResolver {
  const starts = lineStarts(fullContent);

  return (chunk) => {
    if (
      chunk.startIndex !== undefined &&
      chunk.endIndex !== undefined &&
      chunk.endIndex > chunk.startIndex &&
      chunk.endIndex <= fullContent.length
    ) {
      return { start: chunk.startIndex, end: chunk.endIndex };
    }

    const position = chunk.position;
    const [startLine, endLine] = isLinePosition(position) ? lineBounds(position) : [0, 0];

    const start = starts[startLine - 1];

    if (startLine >= 1) {
      if (start === undefined) return null;

      const next = starts[Math.min(endLine, starts.length)];

      return { start, end: next === undefined ? fullContent.length : next - 1 };
    }

    if (!chunk.content) return null;
    const idx = fullContent.indexOf(chunk.content);

    return idx >= 0 ? { start: idx, end: idx + chunk.content.length } : null;
  };
}

function fetchedContentChunk(
  chunk: FetchedChunk,
  resolveRange: RangeResolver | null,
): ContentChunk {
  return {
    id: chunk.id,
    content: chunk.content,
    badge: chunkPositionLabel(chunk.position),
    isFullDocument: chunk.position.type === "full_document",
    range: resolveRange?.(chunk) ?? null,
    originLabel: chunkOriginLabel(chunk),
  };
}

/**
 * What the dialog shows of *filename*: the workspace document and its chunks in managed mode,
 * else the content and chunks the tool outputs fetched, led by the *chunk* it opened on.
 */
export function useDocumentContent({
  filename,
  chunk,
  image,
  isNew,
  isManagedMode,
}: {
  filename: string;
  chunk?: FetchedChunk | null;
  image?: FetchedImage | null;
  isNew?: boolean;
  isManagedMode: boolean;
}): { contentModel: ContentModel; isLoading: boolean; managedLoading: boolean } {
  const { t } = useTranslation();
  const conversationId = useConversationId();
  const workspaces = useWorkspaces();
  const chunks = useFetchedDocumentsStore((state) => state.chunks);
  const documents = useFetchedDocumentsStore((state) => state.documents);
  // Content a tool output already delivered, the only source of a web page.
  const storedFullContent = useFetchedDocumentsStore((state) =>
    filename ? state.documents.get(filename)?.fullContent : undefined,
  );
  // The entry metadata names the original, which a fetched image already shows.
  const wantsMetadata =
    !!filename && !isNew && isWorkspacePath(filename, workspaces) && (isManagedMode || !image);
  const needsContent =
    !!filename && !isNew && !isWebUrl(filename) && (isManagedMode || storedFullContent == null);

  // The workspace queries refetch whenever the workspace changes, so an open
  // dialog follows a reconvert, a rechunk, or an edit by the agent.
  const contentQuery = useQuery({
    ...documentQueries.content(filename, conversationId),
    enabled: needsContent,
  });
  const chunksQuery = useQuery({
    ...documentQueries.chunks(filename),
    enabled: wantsMetadata,
  });

  const metadata = chunksQuery.data ?? null;
  const managedLoading = chunksQuery.isLoading;
  const isLoading = contentQuery.isLoading;

  if (isManagedMode) {
    return {
      isLoading,
      managedLoading,
      contentModel: {
        content: contentQuery.data ?? null,
        chunks:
          metadata?.chunks.map((item, index) => ({
            id: `managed:${index}`,
            content: item.text,
            badge: t(($) => $.documents.dialog.chunkNumber, { index }),
            detail: t(($) => $.documents.dialog.tokens, { count: item.token_count }),
            range: { start: item.start_index, end: item.end_index },
          })) ?? [],
        loading: isLoading || managedLoading,
        error: chunksQuery.error && errorMessage(chunksQuery.error),
        assetsDir: metadata?.assets_dir ?? null,
        preview: entryPreview(filename, metadata),
        metadata,
      },
    };
  }

  const fetchedContent = storedFullContent ?? contentQuery.data ?? null;
  const resolveRange = fetchedContent ? chunkRangeResolver(fetchedContent) : null;
  const document = filename ? documents.get(filename) : undefined;
  const fetched = document ? chunksForDocument(document, chunks) : [];
  if (chunk && !fetched.some((item) => item.id === chunk.id)) fetched.push(chunk);

  return {
    isLoading,
    managedLoading,
    contentModel: {
      content: fetchedContent,
      chunks: sortChunks(fetched).map((item) => fetchedContentChunk(item, resolveRange)),
      loading: isLoading,
      error: null,
      assetsDir: null,
      preview: image
        ? { kind: "media", path: image.filePath, mediaType: image.mediaType, alt: filename }
        : entryPreview(filename, metadata),
      metadata: null,
    },
  };
}
