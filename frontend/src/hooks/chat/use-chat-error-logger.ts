import type { BuildRequestBody } from "@/hooks/chat/use-build-request-body";
import type { UIMessage } from "@ai-sdk/react";
import { useEffect, useEffectEvent } from "react";

/**
 * The backend logs the run error but streams only a generic one, so dumping the
 * full request payload here recovers the exact inputs that caused the failure.
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
