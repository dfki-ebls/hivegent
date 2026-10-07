import { createContext, useContext } from "react";

/** The persisted conversation whose messages and temporary files are being viewed. */
const ConversationIdContext = createContext<string | null>(null);

export const ConversationIdProvider = ConversationIdContext.Provider;

export function useConversationId(): string | null {
  return useContext(ConversationIdContext);
}
