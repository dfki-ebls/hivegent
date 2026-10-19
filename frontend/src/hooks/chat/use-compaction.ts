import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import type { FileUIPart } from "ai";
import { useEffect, useEffectEvent, useRef } from "react";
import { toast } from "@/components/ui/toast";
import { i18n } from "@/i18n";
import { type ChatMessage, type UserTurn, getLastUserMessage } from "@/lib/chat/chat-utils";
import { errorMessage } from "@/lib/utils";
import { conversationMutations } from "@/queries/conversations";
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
  const pendingRetryRef = useRef<UserTurn | undefined>(undefined);
  const activeIdRef = useRef<string | null>(null);

  useEffect(() => {
    activeIdRef.current = id;

    return () => {
      activeIdRef.current = null;
    };
  }, [id]);

  const toastId = (compactedId: string) => `compaction:${compactedId}`;

  const compaction = useMutation({
    ...conversationMutations.compact(),
    onMutate: ({ id: compactedId }) => {
      toast.add({
        id: toastId(compactedId),
        type: "loading",
        title: i18n.t(($) => $.chat.compaction.compacting),
        description: i18n.t(($) => $.chat.compaction.compactingDescription),
      });
    },
    onSuccess: async ({ new_conversation_id }, { id: compactedId, retryLastMessage }) => {
      const open = () =>
        navigate({ to: "/conversations/$id", params: { id: new_conversation_id } });

      // The user moved on while it ran, so offer the result instead of switching.
      if (activeIdRef.current !== compactedId) {
        toast.add({
          id: toastId(compactedId),
          type: "success",
          title: i18n.t(($) => $.chat.compaction.compacted),
          description: i18n.t(($) => $.chat.compaction.compactedDescription),
          actionProps: {
            children: i18n.t(($) => $.common.actions.open),
            onClick: () => void open(),
          },
        });

        return;
      }

      clearAll();
      pendingRetryRef.current = retryLastMessage ? getLastUserMessage(messages) : undefined;
      await open();
      toast.add({
        id: toastId(compactedId),
        type: "success",
        title: i18n.t(($) => $.chat.compaction.compacted),
        description: undefined,
      });
    },
    onError: (error, { id: compactedId }) => {
      toast.add({
        id: toastId(compactedId),
        type: "error",
        title: i18n.t(($) => $.chat.compaction.failed),
        description: errorMessage(error),
      });
    },
  });

  const compact = (retryLastMessage = false) =>
    compaction.mutate({ id, body: buildRequestBody(), retryLastMessage });

  const retry = useEffectEvent((turn: UserTurn) => onRetry(turn.text, turn.files));

  useEffect(() => {
    if (isLoadingHistory || !pendingRetryRef.current) return;
    const turn = pendingRetryRef.current;
    pendingRetryRef.current = undefined;
    retry(turn);
  }, [isLoadingHistory]);

  return { compact, isCompacting: compaction.isPending };
}
