import { createContext, useContext } from "react";

import type { ChatMessage } from "@/lib/chat/chat-utils";

/** The messages of the chat being rendered, read only by what mounts on demand, since it changes with every token. */
const ChatMessagesContext = createContext<ChatMessage[]>([]);

export const ChatMessagesProvider = ChatMessagesContext.Provider;

export function useChatMessages(): ChatMessage[] {
  return useContext(ChatMessagesContext);
}
