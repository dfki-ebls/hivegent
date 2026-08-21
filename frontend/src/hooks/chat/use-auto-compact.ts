import { useNavigate } from "@tanstack/react-router";
import type { FileUIPart } from "ai";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { buildLlmConfig, compactConversation } from "@/lib/api";
import {
  type ChatMessage,
  type UserTurn,
  canCompact,
  getLastUserMessage,
  isContextLengthError,
} from "@/lib/chat/chat-utils";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { useSettingsStore } from "@/stores/settings-store";

const MESSAGE_TOO_LARGE =
  "This message is too large for the model's context window. Compacting earlier history won't help because the message exceeds the limit on its own. Shorten it or split it into smaller parts.";

interface UseAutoCompactArgs {
  id: string;
  /**
   * The failure this session produced (`sessionChatError`), not everything the
   * banner shows. Reopening a conversation whose last turn overflowed must not
   * summarize it and navigate the user to a second conversation they never
   * asked for; only a generation that just failed here earns that.
   */
  chatError: string | undefined;
  messages: ChatMessage[];
  isLoadingHistory: boolean;
  onRetry: (text: string, files?: FileUIPart[]) => void;
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
  // The `<conversation>:<error>` this hook has already acted on; see the
  // trigger effect.
  const handledErrorRef = useRef<string | null>(null);
  const pendingRetryRef = useRef<UserTurn | null>(null);
  const onRetryRef = useRef(onRetry);
  onRetryRef.current = onRetry;
  // Route changes update the active id, while unmounting clears it, so a slow
  // compaction never navigates over wherever the user moved in the meantime.
  const activeIdRef = useRef<string | null>(id);
  activeIdRef.current = id;
  useEffect(() => {
    activeIdRef.current = id;

    return () => {
      activeIdRef.current = null;
    };
  }, [id]);

  const compact = useCallback(
    async (retryTurn?: UserTurn) => {
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
          messages,
        );
        // The user opened another chat while we were summarizing: leave their
        // view untouched and offer the compacted conversation behind an action
        // instead of yanking them to it.
        if (activeIdRef.current !== id) {
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
        if (retryTurn) {
          pendingRetryRef.current = retryTurn;
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
        if (activeIdRef.current === id) {
          setError(new Error(`Couldn't compact the conversation: ${message}`));
        }
      } finally {
        setIsCompacting(false);
      }
    },
    [id, overrides, messages, clearAll, navigate],
  );

  // Act at most once per distinct chat error, tracked on a ref rather than
  // derived from the other state. That one rule replaces the several guards
  // this needs: `isCompacting` flips a render too late to stop a second run
  // (rebuilding `compact` mid-flight, as a settings change does, re-runs this),
  // and gating on `error` would re-enter the moment the banner is cleared.
  // Clearing the ref where the chat error clears keeps the two in step, so the
  // next overflow is handled even when its text repeats.
  useEffect(() => {
    if (!chatError) {
      handledErrorRef.current = null;
      setError(null);
      return;
    }
    if (!isContextLengthError(chatError)) return;
    // Keyed by conversation as well: compaction navigates to the one it minted
    // while this instance stays mounted, and the retried turn there can fail
    // with the very same text — a second conversation's failure, which must be
    // judged on its own (it has one turn to compact, so it is told to shorten
    // the message rather than compacted again).
    const key = `${id}:${chatError}`;
    if (handledErrorRef.current === key) return;
    handledErrorRef.current = key;

    if (!canCompact(messages)) {
      setError(new Error(MESSAGE_TOO_LARGE));
      return;
    }
    void compact(getLastUserMessage(messages));
  }, [id, chatError, compact, messages]);

  useEffect(() => {
    if (isLoadingHistory || !pendingRetryRef.current) return;
    const turn = pendingRetryRef.current;
    pendingRetryRef.current = null;
    onRetryRef.current(turn.text, turn.files);
  }, [isLoadingHistory]);

  const clearError = useCallback(() => setError(null), []);

  return { compact, isCompacting, error, clearError };
}
