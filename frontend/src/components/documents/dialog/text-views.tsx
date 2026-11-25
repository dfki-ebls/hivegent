import { FileQuestionIcon } from "lucide-react";
import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { StatusPanel } from "@/components/common/status-panel";
import {
  ChunkBadges,
  DescriptionBody,
  DescriptionHeading,
  DescriptionPane,
  FileMetaBadges,
  TextPane,
} from "@/components/documents/dialog/parts";
import type { ContentChunk, ContentModel } from "@/components/documents/dialog/model";
import { CodeBlock, CodeBlockContent, CodeBlockHeader } from "@/components/ui/code-block";
import { ScrollArea } from "@/components/ui/scroll-area";
import { WorkspaceMarkdown } from "@/components/workspace-markdown";
import type { DocumentPreview } from "@/lib/original-preview";
import { basename, centerInScroller, cn, isWebUrl } from "@/lib/utils";

type MediaPreview = Extract<DocumentPreview, { kind: "media" }>;

/** The whole document, or the excerpts a tool read of it when its content was not fetched. */
export function FullDocumentText({
  filename,
  contentModel,
  mediaEntry,
}: {
  filename: string;
  contentModel: ContentModel;
  mediaEntry: MediaPreview | null;
}) {
  const { t } = useTranslation();
  const { content, chunks, preview } = contentModel;

  // An uploaded image or video mirrors the extracted-asset view, its description
  // (the source for a markup image) and file metadata badges.
  if (mediaEntry) {
    return (
      <DescriptionPane>
        <DescriptionHeading />
        <DescriptionBody markdown={content} />
        <FileMetaBadges name={basename(mediaEntry.path)} mediaType={mediaEntry.mediaType} />
      </DescriptionPane>
    );
  }

  if (content) {
    return (
      <ScrollArea className="min-h-0 flex-1">
        <WorkspaceMarkdown className="lazy-blocks p-4" documentPath={filename}>
          {content}
        </WorkspaceMarkdown>
      </ScrollArea>
    );
  }

  // No full content fetched, so show the excerpts the model actually
  // read (search snippets, grep hits) instead of a dead end.
  if (chunks.length > 0) {
    return (
      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-4 p-4">
          {chunks.map((item) => (
            <CodeBlock key={item.id} size="sm">
              <CodeBlockHeader className="flex-wrap">
                <ChunkBadges chunk={item} />
              </CodeBlockHeader>
              <CodeBlockContent wrap>{item.content}</CodeBlockContent>
            </CodeBlock>
          ))}
        </div>
      </ScrollArea>
    );
  }

  return (
    <StatusPanel
      icon={FileQuestionIcon}
      title={
        isWebUrl(filename)
          ? t(($) => $.documents.dialog.pageNotFetched)
          : !preview && !filename.toLowerCase().endsWith(".md")
            ? t(($) => $.documents.dialog.binaryFile)
            : t(($) => $.documents.dialog.contentUnavailable)
      }
    />
  );
}

/** The CSS highlight marking the shown chunk, styled in `styles.css`. */
const CHUNK_HIGHLIGHT = "document-chunk";

/**
 * One chunk, highlighted within the full content when that is known.
 * The highlight paints over the unchanged text, so switching chunks never lays out the document again.
 */
export function ChunkText({
  chunk,
  fullContent,
}: {
  chunk: ContentChunk;
  fullContent: string | null;
}) {
  const { t } = useTranslation();
  const textRef = useRef<HTMLSpanElement>(null);
  // The first chunk jumps into view, every later one scrolls there.
  const scrolledRef = useRef(false);
  const start = chunk.range?.start;
  const end = chunk.range?.end;

  useLayoutEffect(() => {
    const span = textRef.current;
    const text = span?.firstChild;
    const viewport = span?.closest<HTMLElement>('[data-slot="code-block-content"]');

    if (start === undefined || end === undefined || !(text instanceof Text) || !viewport) return;

    const range = new Range();
    range.setStart(text, Math.min(start, text.length));
    range.setEnd(text, Math.min(end, text.length));
    CSS.highlights.set(CHUNK_HIGHLIGHT, new Highlight(range));
    centerInScroller(range, viewport, scrolledRef.current ? "smooth" : "instant");
    scrolledRef.current = true;

    return () => {
      CSS.highlights.delete(CHUNK_HIGHLIGHT);
    };
  }, [fullContent, start, end]);

  if (!fullContent) {
    return chunk.content ? (
      <TextPane>{chunk.content}</TextPane>
    ) : (
      <StatusPanel
        icon={FileQuestionIcon}
        title={t(($) => $.documents.dialog.contentUnavailable)}
      />
    );
  }

  return (
    <TextPane ref={textRef} className={cn(chunk.range && "text-muted-foreground")}>
      {fullContent}
    </TextPane>
  );
}
