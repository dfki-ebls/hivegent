import type { UIMessage } from "@ai-sdk/react";
import { useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { buildLlmConfig, compactConversation } from "@/lib/api";
import { canCompact, getLastUserMessage, isContextLengthError } from "@/lib/chat/chat-utils";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { useSettingsStore } from "@/stores/settings-store";

const MESSAGE_TOO_LARGE =
  "This message is too large for the model's context window. Compacting earlier history won't help because the message exceeds the limit on its own. Shorten it or split it into smaller parts.";

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
        // Summarization spans the whole (overflowing) conversation, so it
        // needs the regular model's context window — the aux model is
        // reserved for small scoped tasks like titles and captions.
        const result = await compactConversation(
          id,
          buildLlmConfig(overrides),
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
        const message = err instanceof Error ? err.message : String(err);
        setError(new Error(`Couldn't compact the conversation: ${message}`));
      } finally {
        setIsCompacting(false);
      }
    },
    [id, overrides, clearAll, navigate],
  );

  useEffect(() => {
    if (!isContextLengthError(chatError)) return;
    if (error) return;
    if (!canCompact(messagesRef.current)) {
      setError(new Error(MESSAGE_TOO_LARGE));
      return;
    }
    void compact(getLastUserMessage(messagesRef.current)?.text);
  }, [chatError, compact, error]);

  useEffect(() => {
    if (isLoadingHistory || !pendingRetryRef.current) return;
    const text = pendingRetryRef.current;
    pendingRetryRef.current = null;
    onRetryRef.current(text);
  }, [isLoadingHistory]);

  // Drop a stale recovery error once the chat error clears, which happens when
  // the user sends the next (e.g. shortened) message, so the banner doesn't linger.
  useEffect(() => {
    if (!chatError) setError(null);
  }, [chatError]);

  const clearError = useCallback(() => setError(null), []);

  return { compact, isCompacting, error, clearError };
}
