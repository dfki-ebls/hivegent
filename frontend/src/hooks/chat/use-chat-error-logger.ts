import { useEffect } from "react";
import { useChatContext, useChatSelector } from "@/components/chat/chat-context";

/**
 * The backend logs the run error but streams only a generic one, so dumping the
 * messages here, the selection on their metadata, recovers the inputs that caused the failure.
 */
export function useChatErrorLogger() {
  const { chat, conversationId } = useChatContext();
  const error = useChatSelector((chat) => chat.error);

  useEffect(() => {
    if (error) {
      console.error("Chat request failed", {
        conversationId,
        error,
        messages: chat.messages,
      });
    }
  }, [error, chat, conversationId]);
}
