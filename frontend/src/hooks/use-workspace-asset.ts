import { useQuery } from "@tanstack/react-query";

import { documentQueries } from "@/api/documents";
import { useConversationId } from "@/components/chat/chat-context";
import { useInView } from "@/hooks/use-in-view";
import { useWorkspaces } from "@/hooks/use-workspaces";

/**
 * A workspace asset at *path* as an object URL, fetched once the element bound to `ref` nears
 * the viewport, with the conversation that owns a `/tmp` asset. `null` fetches nothing.
 * Bind `ref` to an element that stays mounted while loading.
 */
export function useWorkspaceAsset(path: string | null) {
  const conversationId = useConversationId();
  const workspaces = useWorkspaces();
  const [ref, inView] = useInView();
  const { data, isError } = useQuery({
    ...documentQueries.asset(path ?? "", conversationId, workspaces),
    enabled: path !== null && inView,
  });

  return { ref, url: data ?? null, error: isError };
}
