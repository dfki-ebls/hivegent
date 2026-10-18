import { useTranslation } from "react-i18next";

import { useWorkspaceAsset } from "@/hooks/use-workspace-asset";
import { cn } from "@/lib/utils";

interface AssetImageProps {
  /** Canonical workspace path of the image. */
  filePath: string;
  alt?: string;
  /** Classes for the <img> element (object-fit, sizing). */
  className?: string;
  /** Classes for the observed wrapper, which also sizes the loading skeleton. */
  wrapperClassName?: string;
}

/** Lazily fetches a workspace asset and renders it once scrolled into view. */
export function AssetImage({ filePath, alt, className, wrapperClassName }: AssetImageProps) {
  const { t } = useTranslation();
  const { ref, url, error } = useWorkspaceAsset(filePath);

  return (
    <span
      ref={ref}
      className={cn(
        "relative flex items-center justify-center overflow-hidden text-xs text-muted-foreground",
        wrapperClassName,
      )}
    >
      {error ? (
        t(($) => $.documents.image.unavailable)
      ) : url ? (
        <img src={url} alt={alt ?? filePath} className={cn("block", className)} />
      ) : (
        <span className="absolute inset-0 animate-pulse rounded-[inherit] bg-muted" />
      )}
    </span>
  );
}
