import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useWorkspaceAsset } from "@/hooks/use-workspace-asset";
import { cn } from "@/lib/utils";

interface AssetMediaProps {
  /** Canonical workspace path of the image or video. */
  filePath: string;
  /** Media type of the file, a `video/*` one plays as a video and any other shows as an image. */
  mediaType?: string | null;
  alt?: string;
  /** Classes for the media element (object-fit, sizing). */
  className?: string;
  /** Classes for the observed wrapper, which also sizes the loading skeleton. */
  wrapperClassName?: string;
}

/** Whether the browser can be asked to show a file of *mediaType* inline. */
export function isPreviewable(mediaType: string | null | undefined): mediaType is string {
  return !!mediaType && (mediaType.startsWith("image/") || mediaType.startsWith("video/"));
}

/**
 * Lazily fetches a workspace image or video and renders it once scrolled into view.
 * A format the browser cannot decode (TIFF outside Safari, AVI) falls back like a failed fetch.
 */
export function AssetMedia({
  filePath,
  mediaType,
  alt,
  className,
  wrapperClassName,
}: AssetMediaProps) {
  const { t } = useTranslation();
  const { ref, url, error } = useWorkspaceAsset(filePath);
  const [undecodable, setUndecodable] = useState(false);
  const mediaClassName = cn("block", className);

  return (
    <span
      ref={ref}
      className={cn(
        "relative flex items-center justify-center overflow-hidden text-xs text-muted-foreground",
        wrapperClassName,
      )}
    >
      {error || undecodable ? (
        t(($) => $.documents.image.unavailable)
      ) : url ? (
        mediaType?.startsWith("video/") ? (
          // oxlint-disable-next-line jsx-a11y/media-has-caption -- an uploaded video has no caption track
          <video
            src={url}
            controls
            aria-label={alt ?? filePath}
            className={mediaClassName}
            onError={() => setUndecodable(true)}
          />
        ) : (
          <img
            src={url}
            alt={alt ?? filePath}
            className={mediaClassName}
            onError={() => setUndecodable(true)}
          />
        )
      ) : (
        <span className="absolute inset-0 animate-pulse rounded-[inherit] bg-muted" />
      )}
    </span>
  );
}
