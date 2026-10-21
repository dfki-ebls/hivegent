import { useQuery } from "@tanstack/react-query";
import type { ChatMessage } from "@/lib/chat/chat-utils";
import { useLayoutEffect } from "react";
import { conversationQueries } from "@/queries/conversations";
import { useDraftHandoffStore } from "@/stores/draft-handoff-store";

export function useConversationHistory(
  id: string,
  setMessages: (messages: ChatMessage[]) => void,
  draft = false,
) {
  const takeHandoff = useDraftHandoffStore((state) => state.take);
  // A draft owns its messages in memory, and its id is no row yet. The
  // conversation a draft just became starts from the messages it handed off,
  // taken once per visit, so the freshly streamed turn never flashes through a
  // loading state.
  const { data: history, isPending } = useQuery({
    ...conversationQueries.messages(id),
    initialData: () => takeHandoff(id),
    enabled: !draft,
  });

  // Seeded before paint, so loaded history never shows as an empty chat.
  useLayoutEffect(() => {
    if (history && history.length > 0) setMessages(history);
  }, [history, setMessages]);

  return { isLoadingHistory: !draft && isPending };
}
