import { useCallback } from "react";

import { useObjectUrl } from "@/hooks/use-object-url";
import { fetchDocumentAsset } from "../../lib/api";
import type { FetchedImage } from "../../lib/types";

export function ImageThumb({ image }: { image: FetchedImage }) {
  const fetch = useCallback(() => fetchDocumentAsset(image.filePath), [image.filePath]);
  const { url, error } = useObjectUrl(fetch);

  if (error) return null;
  return (
    <div className="ml-4 w-[calc(100%-1rem)]">
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
