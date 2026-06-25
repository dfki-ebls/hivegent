import { useCallback } from "react";

import { useInView } from "@/hooks/use-in-view";
import { useObjectUrl } from "@/hooks/use-object-url";
import { cn } from "@/lib/utils";
import { fetchDocumentAsset } from "../../lib/api";

interface AssetImageProps {
  /** Canonical workspace path of the image. */
  filePath: string;
  alt?: string;
  /** Classes for the <img> element (object-fit, sizing). */
  className?: string;
  /** Classes for the ref'd wrapper that holds the loading skeleton. */
  wrapperClassName?: string;
}

/** Lazily fetches a workspace asset and renders it once scrolled into view. */
export function AssetImage({ filePath, alt, className, wrapperClassName }: AssetImageProps) {
  const fetch = useCallback(
    (signal: AbortSignal) => fetchDocumentAsset(filePath, signal),
    [filePath],
  );
  const [ref, inView] = useInView();
  const { url, error } = useObjectUrl(inView ? fetch : null);

  return (
    <span
      ref={ref}
      className={cn(
        "flex items-center justify-center overflow-hidden bg-muted/40 text-[10px] text-muted-foreground",
        !url && !error && "animate-pulse",
        wrapperClassName,
      )}
    >
      {url && !error ? (
        <img src={url} alt={alt ?? filePath} className={cn("block", className)} />
      ) : error ? (
        "unavailable"
      ) : null}
    </span>
  );
}
