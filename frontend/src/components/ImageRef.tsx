"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { fetchDocumentAsset } from "@/lib/api";

/**
 * Inline image rendered by Streamdown for `<imgref>` tags.
 *
 * Fetches the image from the authenticated document API and displays it
 * as a `<figure>` with an optional caption.
 *
 * Since `imgref` is not a native HTML element, Streamdown's `Components`
 * type matches it via the string index signature which expects
 * `Record<string, unknown> & ExtraProps`.
 */
interface ImageRefProps {
  src?: string;
  children?: ReactNode;
  node?: unknown;
  [key: string]: unknown;
}

export function ImageRef({ src, children }: ImageRefProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!src) return;
    let cancelled = false;

    fetchDocumentAsset(src)
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
  }, [src]);

  useEffect(() => {
    return () => {
      if (blobUrl?.startsWith("blob:")) {
        URL.revokeObjectURL(blobUrl);
      }
    };
  }, [blobUrl]);

  if (!src) return <span>{children}</span>;

  if (error) {
    return (
      <span className="text-sm text-muted-foreground italic">[Image not available: {src}]</span>
    );
  }

  if (!blobUrl) {
    return <span className="text-sm text-muted-foreground italic">Loading image…</span>;
  }

  return (
    <figure className="my-4">
      <img
        src={blobUrl}
        alt={typeof children === "string" ? children : src}
        className="max-w-full rounded-md border"
      />
      {children && (
        <figcaption className="mt-1 text-center text-sm text-muted-foreground">
          {children}
        </figcaption>
      )}
    </figure>
  );
}
