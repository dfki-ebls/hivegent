import { useEffect, useState } from "react";

import { fetchDocumentAsset, fetchGroupDocumentAsset } from "@/lib/api";

interface WorkspaceImageProps {
  /** Image src from markdown (relative path or external URL). */
  src?: string;
  /** Alt text for the image. */
  alt?: string;
  /** Path of the document containing this image reference. */
  documentPath: string;
  /** Group ID when viewing a group document. */
  groupId?: string;
}

/**
 * Renders workspace images with authenticated fetch.
 *
 * Relative paths are resolved against the document's directory, fetched via
 * the authenticated documents API, and displayed using a temporary blob URL.
 */
export function WorkspaceImage({ src, alt, documentPath, groupId }: WorkspaceImageProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!src) return;
    setError(false);
    setBlobUrl(null);

    if (src.startsWith("data:") || src.startsWith("http://") || src.startsWith("https://")) {
      setError(true);
      return;
    }

    // Resolve relative path against document directory.
    const lastSlash = documentPath.lastIndexOf("/");
    const docDir = lastSlash >= 0 ? documentPath.substring(0, lastSlash) : "";
    const resolved = docDir ? `${docDir}/${src}` : src;

    let cancelled = false;

    const fetchImage = groupId
      ? () => fetchGroupDocumentAsset(groupId, resolved)
      : () => fetchDocumentAsset(resolved);

    fetchImage()
      .then((url) => {
        if (!cancelled) {
          setBlobUrl(url);
        } else {
          URL.revokeObjectURL(url);
        }
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });

    return () => {
      cancelled = true;
    };
  }, [src, documentPath, groupId]);

  // Revoke blob URL on unmount.
  useEffect(() => {
    return () => {
      if (blobUrl && blobUrl.startsWith("blob:")) {
        URL.revokeObjectURL(blobUrl);
      }
    };
  }, [blobUrl]);

  if (error || !src) {
    return <span className="text-muted-foreground text-xs">[{alt || "image"}]</span>;
  }

  if (!blobUrl) {
    return <span className="text-muted-foreground text-xs animate-pulse">Loading image...</span>;
  }

  return (
    <img
      src={blobUrl}
      alt={alt ?? ""}
      loading="lazy"
      className="h-auto max-w-full overflow-hidden rounded-md"
    />
  );
}
