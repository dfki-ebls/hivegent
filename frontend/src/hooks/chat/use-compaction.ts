import { useNavigate } from "@tanstack/react-router";
import type { FileUIPart } from "ai";
import { useCallback, useEffect, useEffectEvent, useRef, useState } from "react";
import { toast } from "@/components/ui/toast";
import { i18n } from "@/i18n";
import { compactConversation } from "@/lib/api";
import { type ChatMessage, type UserTurn, getLastUserMessage } from "@/lib/chat/chat-utils";
import { errorMessage } from "@/lib/utils";
import type { BuildRequestBody } from "@/hooks/chat/use-build-request-body";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";

interface UseCompactionArgs {
  id: string;
  messages: ChatMessage[];
  isLoadingHistory: boolean;
  onRetry: (text: string, files?: FileUIPart[]) => void;
  /** The same body a chat turn posts, so compaction can continue that turn. */
  buildRequestBody: BuildRequestBody;
}

export function useCompaction({
  id,
  messages,
  isLoadingHistory,
  onRetry,
  buildRequestBody,
}: UseCompactionArgs) {
  const navigate = useNavigate();
  const clearAll = useFetchedDocumentsStore((state) => state.clearAll);
  const [isCompacting, setIsCompacting] = useState(false);
  const pendingRetryRef = useRef<UserTurn | undefined>(undefined);
  const activeIdRef = useRef<string | null>(null);

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
      toast.add({
        id: toastId,
        type: "loading",
        title: i18n.t(($) => $.chat.compaction.compacting),
        description: i18n.t(($) => $.chat.compaction.compactingDescription),
      });

      try {
        const result = await compactConversation(id, buildRequestBody());

        if (activeIdRef.current !== id) {
          toast.add({
            id: toastId,
            type: "success",
            title: i18n.t(($) => $.chat.compaction.compacted),
            description: i18n.t(($) => $.chat.compaction.compactedDescription),
            actionProps: {
              children: i18n.t(($) => $.common.actions.open),
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
        toast.add({
          id: toastId,
          type: "success",
          title: i18n.t(($) => $.chat.compaction.compacted),
          description: undefined,
        });
      } catch (error) {
        toast.add({
          id: toastId,
          type: "error",
          title: i18n.t(($) => $.chat.compaction.failed),
          description: errorMessage(error),
        });
      } finally {
        setIsCompacting(false);
      }
    },
    [id, buildRequestBody, messages, clearAll, navigate],
  );

  const retry = useEffectEvent((turn: UserTurn) => onRetry(turn.text, turn.files));

  useEffect(() => {
    if (isLoadingHistory || !pendingRetryRef.current) return;
    const turn = pendingRetryRef.current;
    pendingRetryRef.current = undefined;
    retry(turn);
  }, [isLoadingHistory]);

  return { compact, isCompacting };
}
