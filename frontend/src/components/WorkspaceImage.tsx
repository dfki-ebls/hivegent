import { useCallback } from "react";

import { useObjectUrl } from "@/hooks/use-object-url";
import { fetchDocumentAsset } from "@/lib/api";

interface WorkspaceImageProps {
  /** Image src from markdown (relative path or external URL). */
  src?: string;
  /** Alt text for the image. */
  alt?: string;
  /** Canonical path of the containing document (`~/…` or `@<group>/…`). */
  documentPath: string;
}

/**
 * A markdown image src is fetchable only as a workspace-relative path.
 * `URL.canParse` succeeds for anything carrying a scheme (`data:`, `http:`,
 * `file:`, a Windows drive like `T:`), and an absolute root or backslashes also
 * resolve outside the workspace, so all of those are rejected before fetching.
 *
 * This is a client-side fast-fail for UX only, NOT the validation boundary: the
 * backend is authoritative — it strips off-workspace image refs at ingestion
 * (`is_external_ref`) and sanitizes every `/api/documents/...` request
 * (`sanitize_document_path`). Keep that backend validation even if this guard
 * changes; it must never be relied upon as the security check.
 */
function isWorkspaceRelative(src: string): boolean {
  return !URL.canParse(src) && !src.startsWith("/") && !src.includes("\\");
}

/**
 * Renders workspace images with authenticated fetch.
 *
 * Relative paths are resolved against the document's directory, keeping its
 * workspace prefix (`~` or `@<group>`) so the backend routes the fetch to the
 * right scope. Non-relative sources are unsupported and render as a fallback.
 */
export function WorkspaceImage({ src, alt, documentPath }: WorkspaceImageProps) {
  const fetch = useCallback(() => {
    if (!src || !isWorkspaceRelative(src)) {
      return Promise.reject(new Error("unsupported image source"));
    }
    // Resolve relative path against the document directory (prefix preserved).
    const lastSlash = documentPath.lastIndexOf("/");
    const docDir = lastSlash >= 0 ? documentPath.substring(0, lastSlash) : "";
    return fetchDocumentAsset(docDir ? `${docDir}/${src}` : src);
  }, [src, documentPath]);

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
