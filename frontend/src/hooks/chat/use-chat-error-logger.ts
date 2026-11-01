import { useEffect } from "react";
import { useChatContext, useChatSelector } from "@/components/chat/chat-context";
import { chatSettings } from "@/hooks/chat/use-hivegent-chat";

/**
 * The backend logs the run error but streams only a generic one, so dumping the
 * full request payload here recovers the exact inputs that caused the failure.
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
        body: chatSettings(),
      });
    }
  }, [error, chat, conversationId]);
}
