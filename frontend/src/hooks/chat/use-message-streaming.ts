import { createContext, useContext } from "react";

/**
 * Whether the chat streams into the message being rendered.
 * A call that never finished is running while it does and was stopped once it does not.
 */
const MessageStreamingContext = createContext(false);

export const MessageStreamingProvider = MessageStreamingContext.Provider;

export function useMessageStreaming(): boolean {
  return useContext(MessageStreamingContext);
}
