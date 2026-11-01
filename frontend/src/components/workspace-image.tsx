import { useTranslation } from "react-i18next";

import { useWorkspaceAsset } from "@/hooks/use-workspace-asset";
import { parentDir } from "@/lib/utils";
import { Spinner } from "@/components/ui/spinner";

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
  const { t } = useTranslation();
  const supported = !!src && isWorkspaceRelative(src);
  const { ref, url, error } = useWorkspaceAsset(supported ? parentDir(documentPath) + src : null);

  if (!supported || error) {
    return (
      <span className="text-xs text-muted-foreground">
        [{alt || t(($) => $.documents.image.fallbackAlt)}]
      </span>
    );
  }

  return (
    <span ref={ref}>
      {url ? (
        <img src={url} alt={alt ?? ""} className="h-auto max-w-full overflow-hidden rounded-md" />
      ) : (
        <ImageLoading />
      )}
    </span>
  );
}

/** The placeholder of a workspace image that is still loading. */
export function ImageLoading() {
  const { t } = useTranslation();

  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      <Spinner aria-hidden />
      {t(($) => $.common.states.loadingImage)}
    </span>
  );
}
