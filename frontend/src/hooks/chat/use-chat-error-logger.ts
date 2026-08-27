import type { BuildRequestBody } from "@/hooks/chat/use-build-request-body";
import type { UIMessage } from "@ai-sdk/react";
import { useEffect, useRef } from "react";

/**
 * pydantic-ai streams errors in-band as ErrorChunks, so the backend has no
 * server-side log of failed runs — dumping the full request payload here is
 * the only way to recover the exact inputs that caused the failure.
 */
export function useChatErrorLogger(
  error: Error | undefined,
  conversationId: string,
  messages: UIMessage[],
  getBody: BuildRequestBody,
) {
  const loggedErrorRef = useRef<unknown>(null);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const getBodyRef = useRef(getBody);
  getBodyRef.current = getBody;

  useEffect(() => {
    if (!error || error === loggedErrorRef.current) return;
    loggedErrorRef.current = error;
    console.error("Chat request failed", {
      conversationId,
      error,
      messages: messagesRef.current,
      body: getBodyRef.current(),
    });
  }, [error, conversationId]);
}
