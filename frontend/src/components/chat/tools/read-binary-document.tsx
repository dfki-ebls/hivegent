import { FileImage, FileText, FileVideo, Paperclip, XCircleIcon } from "lucide-react";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { parseBatch } from "@/components/chat/tools/batch";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { useConversationId } from "@/hooks/chat/use-conversation-id";
import { useObjectUrl } from "@/hooks/use-object-url";
import { keyPrefix } from "@/i18n";
import { fetchDocumentAsset } from "@/lib/api";
import type { SyncOutput, ToolPart } from "@/lib/chat/tool-part";
import { fileStem } from "@/lib/utils";
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

function BinaryMeta({ result }: { result: BinaryReadResult }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
      <span>{result.media_type}</span>
      <span>{formatFileSize(result.size)}</span>
      {result.pages.length > 0 && (
        <span>
          {t(($) => $.pages, {
            count: result.pages.length,
            pages: result.pages.map(formatNumber).join(", "),
          })}
        </span>
      )}
      {result.frames ? <span>{t(($) => $.frames, { count: result.frames })}</span> : null}
      {result.duration ? (
        <span>{t(($) => $.duration, { duration: formatDecimal(result.duration) })}</span>
      ) : null}
      <span className="flex items-center gap-1">
        <Paperclip className="size-3" />
        {t(($) => $.attached)}
      </span>
    </div>
  );
}

/** Inline preview for image binaries, fetched lazily when the tool is expanded. */
function ImagePreview({ result }: { result: BinaryReadResult }) {
  const { t } = useTranslation();
  const conversationId = useConversationId();
  const fetch = useCallback(
    (signal: AbortSignal) => fetchDocumentAsset(result.file_path, signal, conversationId),
    [result.file_path, conversationId],
  );
  const { url, error } = useObjectUrl(fetch);

  return (
    <figure className="space-y-2 rounded-md border bg-muted/40 p-3">
      {url && !error ? (
        <img src={url} alt={result.file_path} className="max-h-96 w-auto max-w-full rounded" />
      ) : (
        <div
          className={`flex h-40 items-center justify-center rounded text-xs text-muted-foreground ${error ? "" : "animate-pulse"}`}
        >
          {error
            ? t(($) => $.chat.tools.binary.previewUnavailable)
            : t(($) => $.common.states.loadingImage)}
        </div>
      )}
      <figcaption className="space-y-1">
        <div className="truncate text-sm font-medium" title={result.file_path}>
          {result.file_path}
        </div>
        <BinaryMeta result={result} />
      </figcaption>
    </figure>
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

function BinaryResult({ result }: { result: BinaryReadResult }) {
  if (result.media_type.startsWith("image/")) return <ImagePreview result={result} />;

  const Icon =
    result.media_type === "application/pdf"
      ? FileText
      : result.media_type.startsWith("video/")
        ? FileVideo
        : FileImage;

  return (
    <div className="flex items-start gap-3 rounded-md border bg-muted/40 p-3 text-sm">
      <Icon className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="truncate font-medium" title={result.file_path}>
          {result.file_path}
        </div>
        <BinaryMeta result={result} />
      </div>
    </div>
  );
}

/** One card per attached file, plus the files the call could not attach. */
export function ReadBinaryDocumentTool({ part, metadata }: ReadBinaryDocumentToolProps) {
  const { results, failures } = parseBatch(metadata, BinaryReadResultSchema);

  return (
    <ToolCard toolName="read_binary_document" part={part}>
      {results.map((result, index) => (
        // A file read twice for different pages is listed twice.
        <BinaryResult key={`${result.file_path}#${index}`} result={result} />
      ))}
      {failures.map((failure) => (
        <div key={failure.item} className="flex items-start gap-2 text-xs text-muted-foreground">
          <XCircleIcon className="mt-0.5 size-3.5 shrink-0 text-destructive" />
          <span className="min-w-0 break-words">
            <span className="font-medium">{failure.item}</span>: {failure.reason}
          </span>
        </div>
      ))}
    </ToolCard>
  );
}
