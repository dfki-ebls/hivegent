import { useMemo } from "react";
import { useChatContext } from "@/components/chat/chat-context";
import { type ChatRequestConfig, stopTurn } from "@/api/conversations";
import { toast } from "@/components/ui/toast";
import { joinTextParts, lastUserIndex, resendOf } from "@/lib/chat/chat-utils";
import type { ChatMessage } from "@/lib/chat/message";
import { errorMessage } from "@/lib/utils";

/** The actions that rewrite the transcript of the chat being rendered. */
export function useChatActions() {
  const { chat, conversationId } = useChatContext();

  return useMemo(
    () => ({
      /** Replace *message* and everything after it with its edit to *text*. */
      edit: (message: ChatMessage, text: string) => {
        void chat.sendMessage(resendOf(message, text));
      },
      /** Send the last user message again, after a turn that failed. */
      retry: () => {
        const last = chat.messages[lastUserIndex(chat.messages)];

        if (!last) return;

        void chat.sendMessage(resendOf(last, joinTextParts(last.parts) ?? ""));
      },
      /** Answer the last user message anew. */
      regenerate: () => void chat.regenerate(),
      /** Summarize the history, streamed into the last reply or a new one after a failed prompt. */
      compact: () =>
        void chat.sendMessage(undefined, {
          body: { compact: true } satisfies Partial<ChatRequestConfig>,
        }),
      /**
       * Stop the turn at the server, which runs it on when the client only stops reading.
       * Its stream then ends by itself once the turn is stored, so nothing queued is sent
       * while the server holds the turn, and the client stops reading only when that failed.
       */
      stop: () => {
        if (!conversationId) return void chat.stop();

        void stopTurn(conversationId).catch((error: unknown) => {
          toast.add({ type: "error", title: errorMessage(error) });
          void chat.stop();
        });
      },
    }),
    [chat, conversationId],
  );
}
