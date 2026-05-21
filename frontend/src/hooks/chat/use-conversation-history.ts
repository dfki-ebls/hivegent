import type { UIMessage } from "@ai-sdk/react";
import { useEffect, useRef, useState } from "react";
import { getConversation, getConversationMessages } from "@/lib/api";

export function useConversationHistory(id: string, setMessages: (messages: UIMessage[]) => void) {
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [compactedFrom, setCompactedFrom] = useState<string | null>(null);
  const setMessagesRef = useRef(setMessages);
  setMessagesRef.current = setMessages;

  useEffect(() => {
    let cancelled = false;
    setIsLoadingHistory(true);
    setCompactedFrom(null);
    void (async () => {
      try {
        // POST /conversations only reserves an ID — the DB row isn't
        // written until the first message. Fetch messages first so empty
        // conversations skip the summary call (which would 404).
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
  }, [id]);

  return { isLoadingHistory, compactedFrom };
}
