"use client";

import { useCallback } from "react";
import { useObjectUrl } from "@/hooks/use-object-url";
import { fetchDocumentAsset } from "@/lib/api";

/**
 * Inline image rendered by Streamdown for `<imgref>` tags.
 *
 * A self-contained void marker: the `src` attribute is the workspace asset path
 * and `alt` is the optional caption.  Fetches the image from the authenticated
 * document API and displays it as a `<figure>`.
 */
interface ImageRefProps {
  src?: string;
  alt?: string;
  node?: unknown;
  [key: string]: unknown;
}

export function ImageRef({ src, alt }: ImageRefProps) {
  const fetch = useCallback(() => fetchDocumentAsset(src ?? ""), [src]);
  const { url, error } = useObjectUrl(src ? fetch : null);

  if (!src) return null;

  if (error) {
    return (
      <span className="text-sm text-muted-foreground italic">[Image not available: {src}]</span>
    );
  }

  if (!url) {
    return <span className="text-sm text-muted-foreground italic">Loading image…</span>;
  }

  return (
    <figure className="my-4">
      <img src={url} alt={alt ?? src} className="max-w-full rounded-md border" />
      {alt && (
        <figcaption className="mt-1 text-center text-sm text-muted-foreground">{alt}</figcaption>
      )}
    </figure>
  );
}
