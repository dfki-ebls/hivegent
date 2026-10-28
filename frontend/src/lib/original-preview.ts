import type { Sheet } from "read-excel-file/browser";

/** An original the browser renders through a parser rather than as plain media. */
export type DocumentKind = "pdf" | "docx" | "xlsx";

/** What the Preview tab of a document shows. */
export type DocumentPreview =
  | { kind: "media"; path: string; mediaType?: string | null; alt: string }
  | { kind: DocumentKind; documentPath: string };

const DOCUMENT_KINDS: readonly DocumentKind[] = ["pdf", "docx", "xlsx"];

/** The parsed original per kind, as the Preview tab renders it. */
export interface OriginalContent {
  pdf: Blob;
  docx: string;
  xlsx: Sheet[];
}

/**
 * The preview of a file by its media type or extension.
 * Keyed on the original, so an SVG indexed as text previews as an image.
 */
export function previewKind(
  mediaType: string | null | undefined,
  path: string | null | undefined,
): "media" | DocumentKind | null {
  if (mediaType?.startsWith("image/") || mediaType?.startsWith("video/")) return "media";
  if (mediaType === "application/pdf") return "pdf";

  const extension = path?.slice(path.lastIndexOf(".") + 1).toLowerCase() ?? "";

  return DOCUMENT_KINDS.find((kind) => kind === extension) ?? null;
}

/** The parsed original per kind, its library loaded on first use. */
export const parseOriginal: {
  [K in DocumentKind]: (blob: Blob) => Promise<OriginalContent[K]>;
} = {
  // Retyped so the frame showing it never renders other content.
  pdf: async (blob) => new Blob([blob], { type: "application/pdf" }),
  docx: async (blob) => {
    const [{ default: mammoth }, { default: DOMPurify }] = await Promise.all([
      import("mammoth"),
      import("dompurify"),
    ]);
    const { value } = await mammoth.convertToHtml({ arrayBuffer: await blob.arrayBuffer() });

    return DOMPurify.sanitize(value);
  },
  xlsx: async (blob) => (await import("read-excel-file/browser")).default(blob),
};
