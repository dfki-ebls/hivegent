import type { ChunkedDocumentResponse } from "@/api/documents";
import type { DocumentPreview } from "@/lib/original-preview";
import type { PreviewTab } from "@/components/documents/preview-tabs";

/** What the main pane shows. */
export type View =
  | { mode: "full-doc" }
  | { mode: "chunk"; id: string }
  | { mode: "asset"; index: number }
  | { mode: "edit" };
export type SidebarTab = "chunks" | "assets";

export interface ContentChunk {
  id: string;
  content: string;
  badge: string;
  detail?: string;
  isFullDocument?: boolean;
  range: { start: number; end: number } | null;
  originLabel?: string;
}

export interface ContentModel {
  content: string | null;
  chunks: ContentChunk[];
  loading: boolean;
  error: string | null;
  assetsDir: string | null;
  /** The original, when the browser can render it in the preview tab. */
  preview: DocumentPreview | null;
  metadata: ChunkedDocumentResponse | null;
}

/** A highlighted chunk opens on its text, anything else on the rendered original. */
export function defaultTab(view: View): PreviewTab {
  return view.mode === "chunk" ? "text" : "preview";
}
