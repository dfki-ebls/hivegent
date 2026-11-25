import { FileQuestionIcon } from "lucide-react";
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
import { basename, cn, isWebUrl } from "@/lib/utils";

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
        <WorkspaceMarkdown className="p-4" documentPath={filename}>
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

/** One chunk, highlighted within the full content when that is known. */
export function ChunkText({
  chunk,
  fullContent,
  highlightRef,
}: {
  chunk: ContentChunk;
  fullContent: string | null;
  highlightRef: (node: HTMLElement | null) => void;
}) {
  const { t } = useTranslation();

  if (!fullContent) {
    return chunk.content ? (
      <TextPane>{chunk.content}</TextPane>
    ) : (
      <StatusPanel icon={FileQuestionIcon} title={t(($) => $.documents.dialog.contentUnavailable)} />
    );
  }

  if (!chunk.range) return <TextPane>{fullContent}</TextPane>;

  const { start, end } = chunk.range;
  // Whole lines are marked as one block, which paints and measures as a single box
  // however many thousand lines it spans, and whose own break replaces the newlines around it.
  const wholeLines =
    (start === 0 || fullContent[start - 1] === "\n") &&
    (end === fullContent.length || fullContent[end] === "\n");
  const gap = wholeLines ? 1 : 0;

  return (
    <TextPane>
      <span className="text-muted-foreground">{fullContent.slice(0, Math.max(0, start - gap))}</span>
      <mark
        key={chunk.id}
        ref={highlightRef}
        className={cn(
          "rounded-sm bg-primary/15 text-foreground",
          wholeLines ? "block" : "box-decoration-clone",
        )}
      >
        {fullContent.slice(start, end)}
      </mark>
      <span className="text-muted-foreground">{fullContent.slice(end + gap)}</span>
    </TextPane>
  );
}
