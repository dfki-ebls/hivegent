"use client";

import { useCallback } from "react";
import { useInView } from "@/hooks/use-in-view";
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
  const fetch = useCallback((signal: AbortSignal) => fetchDocumentAsset(src ?? "", signal), [src]);
  const [ref, inView] = useInView();
  const { url, error } = useObjectUrl(src && inView ? fetch : null);

  if (!src) return null;

  if (error) {
    return (
      <span className="text-sm text-muted-foreground italic">[Image not available: {src}]</span>
    );
  }

  // The figure stays mounted across the loading transition so the observer
  // keeps a stable target and the fetched object URL is not revoked early.
  return (
    <figure ref={ref} className="my-4">
      {url ? (
        <>
          <img src={url} alt={alt ?? src} className="max-w-full rounded-md border" />
          {alt && (
            <figcaption className="mt-1 text-center text-sm text-muted-foreground">
              {alt}
            </figcaption>
          )}
        </>
      ) : (
        <span className="text-sm text-muted-foreground italic">Loading image…</span>
      )}
    </figure>
  );
}
