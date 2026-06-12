import { useCallback } from "react";

import { useInView } from "@/hooks/use-in-view";
import { useObjectUrl } from "@/hooks/use-object-url";
import { fetchDocumentAsset } from "../../lib/api";
import type { FetchedImage } from "../../lib/types";

export function ImageThumb({ image }: { image: FetchedImage }) {
  const fetch = useCallback(
    (signal: AbortSignal) => fetchDocumentAsset(image.filePath, signal),
    [image.filePath],
  );
  const [ref, inView] = useInView();
  const { url, error } = useObjectUrl(inView ? fetch : null);

  if (error) return null;
  return (
    <div ref={ref} className="ml-4 w-[calc(100%-1rem)]">
      {url ? (
        <img
          src={url}
          alt={image.filePath}
          className="max-h-64 w-auto max-w-full rounded-md border"
        />
      ) : (
        <div className="h-32 animate-pulse rounded-md border bg-muted/40" />
      )}
    </div>
  );
}
