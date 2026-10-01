import type { BuildRequestBody } from "@/hooks/chat/use-build-request-body";
import type { UIMessage } from "@ai-sdk/react";
import { useEffect, useEffectEvent } from "react";

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
  const logError = useEffectEvent(() => {
    console.error("Chat request failed", { conversationId, error, messages, body: getBody() });
  });

  useEffect(() => {
    if (error) logError();
  }, [error]);
}
