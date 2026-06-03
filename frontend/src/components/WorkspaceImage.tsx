import { useCallback } from "react";

import { useObjectUrl } from "@/hooks/use-object-url";
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
 * External (`data:`/`http:`) sources are unsupported and render as a fallback.
 */
export function WorkspaceImage({ src, alt, documentPath, groupId }: WorkspaceImageProps) {
  const fetch = useCallback(() => {
    if (!src || src.startsWith("data:") || src.startsWith("http://") || src.startsWith("https://")) {
      return Promise.reject(new Error("unsupported image source"));
    }
    // Resolve relative path against document directory.
    const lastSlash = documentPath.lastIndexOf("/");
    const docDir = lastSlash >= 0 ? documentPath.substring(0, lastSlash) : "";
    const resolved = docDir ? `${docDir}/${src}` : src;
    return groupId ? fetchGroupDocumentAsset(groupId, resolved) : fetchDocumentAsset(resolved);
  }, [src, documentPath, groupId]);

  const { url, error } = useObjectUrl(src ? fetch : null);

  if (error || !src) {
    return <span className="text-muted-foreground text-xs">[{alt || "image"}]</span>;
  }

  if (!url) {
    return <span className="text-muted-foreground text-xs animate-pulse">Loading image...</span>;
  }

  return (
    <img
      src={url}
      alt={alt ?? ""}
      loading="lazy"
      className="h-auto max-w-full overflow-hidden rounded-md"
    />
  );
}
