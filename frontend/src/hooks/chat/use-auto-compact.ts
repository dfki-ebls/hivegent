import type { UIMessage } from "@ai-sdk/react";
import { useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { buildAuxLlmConfig, compactConversation } from "@/lib/api";
import { getLastUserMessage, isContextLengthError } from "@/lib/chat/chat-utils";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { useSettingsStore } from "@/stores/settings-store";

interface UseAutoCompactArgs {
  id: string;
  chatError: Error | undefined;
  messages: UIMessage[];
  isLoadingHistory: boolean;
  onRetry: (text: string) => void;
}

export function useAutoCompact({
  id,
  chatError,
  messages,
  isLoadingHistory,
  onRetry,
}: UseAutoCompactArgs) {
  const navigate = useNavigate();
  const clearAll = useFetchedDocumentsStore((state) => state.clearAll);
  const overrides = useSettingsStore((state) => state.overrides);
  const [isCompacting, setIsCompacting] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const pendingRetryRef = useRef<string | null>(null);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const onRetryRef = useRef(onRetry);
  onRetryRef.current = onRetry;

  const compact = useCallback(
    async (retryMessageText?: string) => {
      setIsCompacting(true);
      setError(null);
      try {
        const result = await compactConversation(
          id,
          buildAuxLlmConfig(overrides),
          messagesRef.current,
        );
        clearAll();
        if (retryMessageText) {
          pendingRetryRef.current = retryMessageText;
        }
        await navigate({
          to: "/conversations/$id",
          params: { id: result.new_conversation_id },
        });
      } catch (err) {
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        setIsCompacting(false);
      }
    },
    [id, overrides, clearAll, navigate],
  );

  useEffect(() => {
    if (!isContextLengthError(chatError)) return;
    if (error) return;
    void compact(getLastUserMessage(messagesRef.current)?.text);
  }, [chatError, compact, error]);

  useEffect(() => {
    if (isLoadingHistory || !pendingRetryRef.current) return;
    const text = pendingRetryRef.current;
    pendingRetryRef.current = null;
    onRetryRef.current(text);
  }, [isLoadingHistory]);

  const clearError = useCallback(() => setError(null), []);

  return { compact, isCompacting, error, clearError };
}
