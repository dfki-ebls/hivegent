import { FileTextIcon, FileVideoIcon, ImageOffIcon } from "lucide-react";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { parseBatch } from "@/components/chat/tools/batch";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import {
  Attachment,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentMedia,
  AttachmentTitle,
} from "@/components/ui/attachment";
import { Spinner } from "@/components/ui/spinner";
import { useConversationId } from "@/hooks/chat/use-conversation-id";
import { useObjectUrl } from "@/hooks/use-object-url";
import { keyPrefix } from "@/i18n";
import { fetchDocumentAsset } from "@/lib/api";
import type { SyncOutput, ToolPart } from "@/lib/chat/tool-part";
import { basename, fileStem } from "@/lib/utils";
import { formatDecimal, formatFileSize, formatNumber } from "@/i18n/format";

const T_OPTIONS = keyPrefix(($) => $.chat.tools.binary);

/** Mirrors `BinaryReadResult` in `backend/src/hivegent/tools/binary.py`. */
const BinaryReadResultSchema = z.object({
  file_path: z.string(),
  media_type: z.string(),
  size: z.number(),
  pages: z.array(z.number()),
  frames: z.number().optional(),
  duration: z.number().nullable().optional(),
});
type BinaryReadResult = z.infer<typeof BinaryReadResultSchema>;

/** The media type, size, and the pages, frames, or duration the model was given, in one line. */
function BinaryMeta({ result }: { result: BinaryReadResult }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const parts = [
    result.media_type,
    formatFileSize(result.size),
    result.pages.length > 0 &&
      t(($) => $.pages, {
        count: result.pages.length,
        pages: result.pages.map(formatNumber).join(", "),
      }),
    result.frames && t(($) => $.frames, { count: result.frames }),
    result.duration && t(($) => $.duration, { duration: formatDecimal(result.duration) }),
  ];

  return <AttachmentDescription>{parts.filter(Boolean).join(" · ")}</AttachmentDescription>;
}

/** An image's pixels, fetched once the line opens since the content only mounts then. */
function ImagePreview({ result }: { result: BinaryReadResult }) {
  const { t } = useTranslation();
  const conversationId = useConversationId();
  const fetch = useCallback(
    (signal: AbortSignal) => fetchDocumentAsset(result.file_path, signal, conversationId),
    [result.file_path, conversationId],
  );
  const { url, error } = useObjectUrl(fetch);

  if (url) return <img src={url} alt={result.file_path} />;

  return error ? (
    <ImageOffIcon aria-label={t(($) => $.chat.tools.binary.previewUnavailable)} />
  ) : (
    <Spinner />
  );
}

/** A file the call attached to the model, an image with its preview and any other file with its kind. */
function BinaryAttachment({ result }: { result: BinaryReadResult }) {
  const image = result.media_type.startsWith("image/");

  return (
    <Attachment orientation={image ? "vertical" : "horizontal"} title={result.file_path}>
      <AttachmentMedia variant={image ? "image" : "icon"}>
        {image ? (
          <ImagePreview result={result} />
        ) : result.media_type.startsWith("video/") ? (
          <FileVideoIcon />
        ) : (
          <FileTextIcon />
        )}
      </AttachmentMedia>
      <AttachmentContent>
        <AttachmentTitle>{basename(result.file_path)}</AttachmentTitle>
        <BinaryMeta result={result} />
      </AttachmentContent>
    </Attachment>
  );
}

/** Description markdown path for an image, mirroring the backend `<stem>.md` convention. */
function descriptionPath(filePath: string): string {
  return `${fileStem(filePath)}.md`;
}

/**
 * Surface image binaries in the context panel, keyed by their description
 * path so they merge with the caption document (same stem) when both are read.
 * Non-image binaries (PDFs, videos) are skipped — they have no inline preview yet.
 *
 * Deliberately, a thumbnail appears only when the image was read *as a binary*
 * (its pixels entered the model's context). A caption retrieved by search that
 * merely references an image (via its `image_path`) does not surface one: the
 * fetched view mirrors what the model actually saw, not what it could have.
 */
export const syncReadBinaryDocumentOutput: SyncOutput = ({ metadata, addImage }) => {
  for (const result of parseBatch(metadata, BinaryReadResultSchema).results) {
    if (!result.media_type.startsWith("image/")) continue;

    addImage(descriptionPath(result.file_path), {
      filePath: result.file_path,
      mediaType: result.media_type,
    });
  }
};

interface ReadBinaryDocumentToolProps {
  part: ToolPart;
  metadata: unknown;
}

/** The files the call attached, plus the ones it could not attach and why. */
export function ReadBinaryDocumentTool({ part, metadata }: ReadBinaryDocumentToolProps) {
  const { results, failures } = parseBatch(metadata, BinaryReadResultSchema);

  return (
    <ToolCard toolName="read_binary_document" part={part}>
      {results.length + failures.length > 0 && (
        <AttachmentGroup className="flex-wrap">
          {results.map((result, index) => (
            // A file read twice for different pages is listed twice.
            <BinaryAttachment key={`${result.file_path}#${index}`} result={result} />
          ))}
          {failures.map((failure) => (
            <Attachment key={failure.item} state="error" title={failure.item}>
              <AttachmentMedia>
                <FileTextIcon />
              </AttachmentMedia>
              <AttachmentContent>
                <AttachmentTitle>{basename(failure.item)}</AttachmentTitle>
                <AttachmentDescription>{failure.reason}</AttachmentDescription>
              </AttachmentContent>
            </Attachment>
          ))}
        </AttachmentGroup>
      )}
    </ToolCard>
  );
}
