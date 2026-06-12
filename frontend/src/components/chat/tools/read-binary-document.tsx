import { FileImage, FileText, FileVideo, Paperclip } from "lucide-react";
import { useCallback } from "react";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";
import { ToolParameters } from "@/components/ToolDisplay";
import { useStayScrolledOnToggle } from "@/hooks/chat/use-stay-scrolled-on-toggle";
import { useObjectUrl } from "@/hooks/use-object-url";
import { fetchDocumentAsset } from "@/lib/api";
import { parseJson, type SyncOutput, type ToolPart } from "@/lib/chat/tool-part";
import { fileStem, formatFileSize } from "@/lib/utils";

interface BinaryReadResult {
  file_path: string;
  media_type: string;
  size: number;
  pages: number[];
  frames?: number;
  duration?: number | null;
}

function isBinaryReadResult(value: unknown): value is BinaryReadResult {
  if (value == null || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.file_path === "string" &&
    typeof v.media_type === "string" &&
    typeof v.size === "number" &&
    Array.isArray(v.pages)
  );
}

function BinaryMeta({ result }: { result: BinaryReadResult }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
      <span>{result.media_type}</span>
      <span>{formatFileSize(result.size)}</span>
      {result.pages.length > 0 && <span>pages {result.pages.join(", ")}</span>}
      {result.frames ? <span>{result.frames} frames sampled</span> : null}
      {result.duration ? <span>{result.duration.toFixed(1)}s</span> : null}
      <span className="flex items-center gap-1">
        <Paperclip className="size-3" />
        attached to model
      </span>
    </div>
  );
}

/** Inline preview for image binaries, fetched lazily when the tool is expanded. */
function ImagePreview({ result }: { result: BinaryReadResult }) {
  const fetch = useCallback(() => fetchDocumentAsset(result.file_path), [result.file_path]);
  const { url, error } = useObjectUrl(fetch);

  return (
    <figure className="space-y-2 rounded-md border bg-muted/40 p-3">
      {url && !error ? (
        <img src={url} alt={result.file_path} className="max-h-96 w-auto max-w-full rounded" />
      ) : (
        <div
          className={`flex h-40 items-center justify-center rounded text-xs text-muted-foreground ${error ? "" : "animate-pulse"}`}
        >
          {error ? "Preview unavailable" : "Loading image…"}
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
 * Surface image binaries in the fetched panel, keyed by their description
 * path so they merge with the caption document (same stem) when both are read.
 * Non-image binaries (PDFs, videos) are skipped — they have no inline preview yet.
 *
 * Deliberately, a thumbnail appears only when the image was read *as a binary*
 * (its pixels entered the model's context). A caption retrieved by search that
 * merely references an image (via its `image_path`) does not surface one: the
 * fetched view mirrors what the model actually saw, not what it could have.
 */
export const syncReadBinaryDocumentOutput: SyncOutput = (
  _input,
  _text,
  metadata,
  _addChunk,
  _markFullDocument,
  addImage,
) => {
  const result = isBinaryReadResult(metadata) ? metadata : null;
  if (!result || !result.media_type.startsWith("image/")) return;
  addImage(descriptionPath(result.file_path), {
    filePath: result.file_path,
    mediaType: result.media_type,
  });
};

interface ReadBinaryDocumentToolProps {
  part: ToolPart;
  metadata: unknown;
}

export function ReadBinaryDocumentTool({ part, metadata }: ReadBinaryDocumentToolProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = parseJson<Record<string, unknown>>(part.input);
  const result = isBinaryReadResult(metadata) ? metadata : null;
  const Icon =
    result?.media_type === "application/pdf"
      ? FileText
      : result?.media_type.startsWith("video/")
        ? FileVideo
        : FileImage;
  const stayScrolled = useStayScrolledOnToggle();

  return (
    <Tool defaultOpen={false} onOpenChange={stayScrolled}>
      <ToolHeader title="Read Binary Document" type="tool-read_binary_document" state={state} />
      <ToolContent>
        {input && <ToolParameters params={input} />}
        {result &&
          (result.media_type.startsWith("image/") ? (
            <ImagePreview result={result} />
          ) : (
            <div className="flex items-start gap-3 rounded-md border bg-muted/40 p-3 text-sm">
              <Icon className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1 space-y-1">
                <div className="truncate font-medium" title={result.file_path}>
                  {result.file_path}
                </div>
                <BinaryMeta result={result} />
              </div>
            </div>
          ))}
      </ToolContent>
    </Tool>
  );
}
