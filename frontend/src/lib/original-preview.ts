import type { Sheet } from "read-excel-file/browser";

/** An original the browser renders through a parser rather than as plain media. */
export type DocumentKind = "pdf" | "docx" | "xlsx";

/** An image or video, which its text describes, so both show together. */
export type MediaPreview = { kind: "media"; path: string; mediaType?: string | null; alt: string };

/** A document, which its text converts, so the original shows in a tab of its own. */
export type ParsedPreview = { kind: DocumentKind; documentPath: string };

/** The original of a document as the dialog shows it. */
export type DocumentPreview = MediaPreview | ParsedPreview;

const DOCUMENT_KINDS: readonly DocumentKind[] = ["pdf", "docx", "xlsx"];

/** The parsed original per kind, as the Preview tab renders it. */
export interface OriginalContent {
  /** An object URL, which the query cache revokes with its entry. */
  pdf: string;
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

/** What the worker parses each kind to, which the page finishes where it needs the DOM. */
export interface WorkerParsed {
  docx: string;
  xlsx: Sheet[];
}

export type WorkerRequest = {
  [K in keyof WorkerParsed]: { kind: K; blob: Blob };
}[keyof WorkerParsed];
export type WorkerReply<T> = { value: T } | { error: string };

/** *blob* parsed as *kind* by a worker of its own, which *signal* terminates. */
function parseInWorker<K extends keyof WorkerParsed>(
  kind: K,
  blob: Blob,
  signal?: AbortSignal,
): Promise<WorkerParsed[K]> {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const worker = new Worker(new URL("./original-worker.ts", import.meta.url), {
      type: "module",
    });
    const settle = (finish: () => void) => {
      worker.terminate();
      signal?.removeEventListener("abort", abort);
      finish();
    };
    const abort = () => settle(() => reject(signal?.reason));

    signal?.addEventListener("abort", abort, { once: true });
    worker.addEventListener("message", ({ data }: MessageEvent<WorkerReply<WorkerParsed[K]>>) =>
      settle(() => ("value" in data ? resolve(data.value) : reject(new Error(data.error)))),
    );
    worker.addEventListener("error", (event) => settle(() => reject(new Error(event.message))));
    worker.postMessage({ kind, blob });
  });
}

/** The parsed original per kind, its library loaded on first use, away from the page where it can. */
export const parseOriginal: {
  [K in DocumentKind]: (blob: Blob, signal?: AbortSignal) => Promise<OriginalContent[K]>;
} = {
  // Retyped so the frame showing it never renders other content.
  pdf: async (blob) => URL.createObjectURL(new Blob([blob], { type: "application/pdf" })),
  docx: async (blob, signal) => {
    const [value, { default: DOMPurify }] = await Promise.all([
      parseInWorker("docx", blob, signal),
      import("dompurify"),
    ]);
    // Neither styles, forms, nor ids reach the app's page, and links leave it in a new tab.
    const html = DOMPurify.sanitize(value, {
      FORBID_TAGS: ["style", "form"],
      FORBID_ATTR: ["style", "id", "name"],
      RETURN_DOM_FRAGMENT: true,
    });

    for (const link of html.querySelectorAll("a")) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }

    const container = document.createElement("div");
    container.append(html);

    return container.innerHTML;
  },
  xlsx: (blob, signal) => parseInWorker("xlsx", blob, signal),
};
