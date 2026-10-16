import { useTranslation } from "react-i18next";

import { useWorkspaceAsset } from "@/hooks/use-workspace-asset";
import { ImageLoading } from "@/components/WorkspaceImage";

/**
 * Inline image rendered by Streamdown for `<imgref>` tags.
 *
 * A self-contained void marker: the `src` attribute is the workspace asset path
 * and `alt` is the optional caption. Fetches the image from the authenticated
 * document API and displays it as a `<figure>`.
 */
export function ImageRef({ src, alt }: { src?: string; alt?: string; [attribute: string]: unknown }) {
  const { t } = useTranslation();
  const { ref, url, error } = useWorkspaceAsset(src ?? null);

  if (!src) return null;

  if (error) {
    return (
      <span className="text-sm text-muted-foreground italic">
        {t(($) => $.chat.image.unavailable, { src })}
      </span>
    );
  }

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
        <ImageLoading />
      )}
    </figure>
  );
}
