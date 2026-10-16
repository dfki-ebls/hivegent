import { useCallback } from "react";
import { useConversationId } from "@/hooks/chat/use-conversation-id";
import { useInView } from "@/hooks/use-in-view";
import { useObjectUrl } from "@/hooks/use-object-url";
import { fetchDocumentAsset } from "@/lib/api";

/**
 * A workspace asset at *path* as an object URL, fetched once the element bound to `ref` nears
 * the viewport, with the conversation that owns a `/tmp` asset. `null` fetches nothing.
 * Bind `ref` to an element that stays mounted while loading, so the URL is not revoked early.
 */
export function useWorkspaceAsset(path: string | null) {
  const conversationId = useConversationId();
  const fetch = useCallback(
    (signal: AbortSignal) => fetchDocumentAsset(path ?? "", signal, conversationId),
    [path, conversationId],
  );
  const [ref, inView] = useInView();
  const { url, error } = useObjectUrl(path && inView ? fetch : null);

  return { ref, url, error };
}
