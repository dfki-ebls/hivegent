import { useChat } from "@ai-sdk/react";
import {
  type FileUIPart,
  DefaultChatTransport,
  lastAssistantMessageIsCompleteWithApprovalResponses,
} from "ai";
import { useCallback, useMemo } from "react";
import { API_BASE_URL, getAuthHeaders } from "@/lib/api";

export interface SendUserMessageInput {
  text: string;
  files?: FileUIPart[];
  messageId?: string;
}

export function useHivegentChat(id: string) {
  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: `${API_BASE_URL}/api/conversations/${id}/chat`,
        headers: () => getAuthHeaders(),
      }),
    [id],
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
