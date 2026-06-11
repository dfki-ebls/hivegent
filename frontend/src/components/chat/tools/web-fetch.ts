import type { SyncOutput } from "@/lib/chat/tool-part";

/** Structured payload of the backend ``web_fetch`` tool. */
interface WebPageData {
  url: string;
  title: string;
  content: string;
  truncated: boolean;
}

export const syncWebFetchOutput: SyncOutput = (
  input,
  _text,
  metadata,
  _addChunk,
  markFullDocument,
) => {
  if (!input) return;
  if (metadata == null || typeof metadata !== "object" || !("content" in metadata)) return;
  const page = metadata as WebPageData;
  if (!page.content) return;
  // Store under the final URL (after redirects) and, when it differs,
  // under the requested URL too — citations may reference either.
  markFullDocument(page.url, page.content, "web_fetch");
  const requested = input.url as string | undefined;
  if (requested && requested !== page.url) {
    markFullDocument(requested, page.content, "web_fetch");
  }
};
