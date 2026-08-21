import { useNavigate } from "@tanstack/react-router";
import type { FileUIPart } from "ai";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { buildLlmConfig, compactConversation } from "@/lib/api";
import { type ChatMessage, type UserTurn, getLastUserMessage } from "@/lib/chat/chat-utils";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
import { useSettingsStore } from "@/stores/settings-store";

interface UseCompactionArgs {
  id: string;
  messages: ChatMessage[];
  isLoadingHistory: boolean;
  onRetry: (text: string, files?: FileUIPart[]) => void;
}

export function useCompaction({
  id,
  messages,
  isLoadingHistory,
  onRetry,
}: UseCompactionArgs) {
  const navigate = useNavigate();
  const clearAll = useFetchedDocumentsStore((state) => state.clearAll);
  const overrides = useSettingsStore((state) => state.overrides);
  const [isCompacting, setIsCompacting] = useState(false);
  const pendingRetryRef = useRef<UserTurn | undefined>(undefined);
  const onRetryRef = useRef(onRetry);
  onRetryRef.current = onRetry;
  const activeIdRef = useRef<string | null>(id);
  activeIdRef.current = id;

  useEffect(() => {
    activeIdRef.current = id;

    return () => {
      activeIdRef.current = null;
    };
  }, [id]);

  const compact = useCallback(
    async (retryLastMessage = false) => {
      setIsCompacting(true);
      const toastId = `compaction:${id}`;
      toast.loading("Compacting conversation", {
        id: toastId,
        description: "Summarizing earlier messages to fit the context window.",
      });

      try {
        const result = await compactConversation(id, buildLlmConfig(overrides), messages);

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
        pendingRetryRef.current = retryLastMessage ? getLastUserMessage(messages) : undefined;
        await navigate({
          to: "/conversations/$id",
          params: { id: result.new_conversation_id },
        });
        toast.success("Conversation compacted", { id: toastId });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        toast.error("Couldn't compact the conversation", { id: toastId, description: message });
      } finally {
        setIsCompacting(false);
      }
    },
    [id, overrides, messages, clearAll, navigate],
  );

  useEffect(() => {
    if (isLoadingHistory || !pendingRetryRef.current) return;
    const turn = pendingRetryRef.current;
    pendingRetryRef.current = undefined;
    onRetryRef.current(turn.text, turn.files);
  }, [isLoadingHistory]);

  return { compact, isCompacting };
}
