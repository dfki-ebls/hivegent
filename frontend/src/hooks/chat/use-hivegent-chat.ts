import { useChat } from "@ai-sdk/react";
import {
  type FileUIPart,
  DefaultChatTransport,
  lastAssistantMessageIsCompleteWithApprovalResponses,
} from "ai";
import { useCallback, useMemo, useRef } from "react";
import { API_BASE_URL, getAuthHeaders } from "@/lib/api";

export interface SendUserMessageInput {
  text: string;
  files?: FileUIPart[];
  messageId?: string;
}

export interface UseHivegentChatOptions {
  /** When set, the first turn is sent to the id-less mint endpoint and the
   * server-issued ID is reported back via `onConversationCreated`. */
  draft?: boolean;
  onConversationCreated?: (id: string) => void;
}

export function useHivegentChat(
  id: string,
  { draft, onConversationCreated }: UseHivegentChatOptions = {},
) {
  const onCreatedRef = useRef(onConversationCreated);
  onCreatedRef.current = onConversationCreated;

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: draft
          ? `${API_BASE_URL}/api/conversations/chat`
          : `${API_BASE_URL}/api/conversations/${id}/chat`,
        headers: () => getAuthHeaders(),
        // The server mints the conversation ID on the first turn and returns
        // it in a response header; capture it so the client can adopt it.
        // Cross-origin reads require the proxy to expose X-Conversation-Id via
        // Access-Control-Expose-Headers; same-origin (the default) needs none.
        fetch: draft
          ? async (input, init) => {
              const res = await fetch(input, init);
              const newId = res.headers.get("X-Conversation-Id");
              if (newId) onCreatedRef.current?.(newId);
              return res;
            }
          : undefined,
      }),
    [id, draft],
  );

  const chat = useChat({
    id,
    transport,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
  });

  const { sendMessage, regenerate } = chat;

  const sendUserMessage = useCallback(
    async (input: SendUserMessageInput, body: Record<string, unknown>) => {
      const headers = await getAuthHeaders();
      const payload = input.messageId
        ? { text: input.text, messageId: input.messageId }
        : { text: input.text, files: input.files };
      await sendMessage(payload, { headers, body });
    },
    [sendMessage],
  );

  const regenerateWithBody = useCallback(
    async (body: Record<string, unknown>) => {
      const headers = await getAuthHeaders();
      await regenerate({ headers, body });
    },
    [regenerate],
  );

  const isStreaming = chat.status === "submitted" || chat.status === "streaming";

  return { ...chat, sendUserMessage, regenerateWithBody, isStreaming };
}
