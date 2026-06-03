"use client";

import type { ReactNode } from "react";
import { useCallback } from "react";
import { useObjectUrl } from "@/hooks/use-object-url";
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
  const fetch = useCallback(() => fetchDocumentAsset(src ?? ""), [src]);
  const { url, error } = useObjectUrl(src ? fetch : null);

  if (!src) return <span>{children}</span>;

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
      <img
        src={url}
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
