import type { ChatMessage } from "@/lib/chat/chat-utils";
import { useEffect, useRef, useState } from "react";
import { getConversation, getConversationMessages } from "@/lib/api";
import { useDraftHandoffStore } from "@/stores/draft-handoff-store";

export function useConversationHistory(
  id: string,
  setMessages: (messages: ChatMessage[]) => void,
  draft = false,
) {
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [compactedFrom, setCompactedFrom] = useState<string | null>(null);
  const setMessagesRef = useRef(setMessages);
  setMessagesRef.current = setMessages;
  const takeHandoff = useDraftHandoffStore((state) => state.take);

  useEffect(() => {
    let cancelled = false;
    setIsLoadingHistory(true);
    setCompactedFrom(null);
    // A draft chat owns its messages in memory — there is no server history
    // to load, and loading the id would 404 since it isn't a row yet.
    if (draft) {
      setIsLoadingHistory(false);
      return;
    }
    // Messages handed off from the draft we just navigated from: seed them
    // directly so the freshly streamed turn doesn't flash a loading state.
    const seeded = takeHandoff(id);
    if (seeded) {
      setMessagesRef.current(seeded);
      setIsLoadingHistory(false);
      return;
    }
    void (async () => {
      try {
        // Fetch messages first and skip the summary call (only needed for
        // the compacted-from banner) when there's nothing to show.
        const initialMessages = await getConversationMessages(id);
        if (cancelled || initialMessages.length === 0) return;
        setMessagesRef.current(initialMessages);
        const conv = await getConversation(id);
        if (cancelled || !conv?.compacted_from) return;
        setCompactedFrom(conv.compacted_from);
      } finally {
        if (!cancelled) {
          setIsLoadingHistory(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, draft, takeHandoff]);

  return { isLoadingHistory, compactedFrom };
}
