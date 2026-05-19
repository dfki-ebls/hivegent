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
    void getConversation(id)
      .then(async (conv) => {
        if (cancelled || !conv) return;
        if (conv.compacted_from) {
          setCompactedFrom(conv.compacted_from);
        }
        const initialMessages = await getConversationMessages(id);
        if (!cancelled && initialMessages.length > 0) {
          setMessagesRef.current(initialMessages);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingHistory(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return { isLoadingHistory, compactedFrom };
}
