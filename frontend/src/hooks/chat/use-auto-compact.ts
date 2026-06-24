import type { UIMessage } from "@ai-sdk/react";
import { useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
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
  // Compaction is slow and the trigger (a greyed-out icon) resets quietly, so
  // before applying the result we check the user is still looking at the chat
  // we compacted, else it would navigate over wherever they moved on to.
  //
  // BOTH refs are required — they catch DISTINCT cases, so don't collapse them:
  //  - currentIdRef: opening another conversation reuses this hook instance and
  //    only changes the id prop, so the live ref diverges from the captured id.
  //  - mountedRef: opening a new draft unmounts this subtree with the id prop
  //    still equal to the captured id (no re-render in between), so currentIdRef
  //    alone would miss it — only the unmount cleanup reveals the user left.
  const currentIdRef = useRef(id);
  currentIdRef.current = id;
  const mountedRef = useRef(true);
  useEffect(() => {
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const compact = useCallback(
    async (retryMessageText?: string) => {
      setIsCompacting(true);
      setError(null);
      // A single toast keyed by the conversation evolves in place from loading
      // to its terminal state, giving feedback that survives the icon resetting.
      const toastId = `compaction:${id}`;
      toast.loading("Compacting conversation", {
        id: toastId,
        description: "Summarizing earlier messages to fit the context window.",
      });
      try {
        // Summarization spans the whole (overflowing) conversation, so it
        // needs the regular model's context window — the aux model is
        // reserved for small scoped tasks like titles and captions.
        const result = await compactConversation(
          id,
          buildLlmConfig(overrides),
          messagesRef.current,
        );
        // The user opened another chat while we were summarizing: leave their
        // view untouched and offer the compacted conversation behind an action
        // instead of yanking them to it.
        if (!mountedRef.current || currentIdRef.current !== id) {
          toast.success("Conversation compacted", {
            id: toastId,
            description: "Open it to continue where this chat left off.",
            action: {
              label: "Open",
              onClick: () =>
                void navigate({
                  to: "/conversations/$id",
                  params: { id: result.new_conversation_id },
                }),
            },
          });
          return;
        }
        clearAll();
        if (retryMessageText) {
          pendingRetryRef.current = retryMessageText;
        }
        await navigate({
          to: "/conversations/$id",
          params: { id: result.new_conversation_id },
        });
        toast.success("Conversation compacted", { id: toastId });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        toast.error("Couldn't compact the conversation", { id: toastId, description: message });
        // Only surface the inline retry banner if this chat is still on screen,
        // otherwise it would attach to whatever conversation the user moved to.
        if (mountedRef.current && currentIdRef.current === id) {
          setError(new Error(`Couldn't compact the conversation: ${message}`));
        }
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
