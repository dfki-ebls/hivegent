import { useMemo } from "react";
import { useChatContext } from "@/components/chat/chat-context";
import type { ChatRequestConfig } from "@/api/conversations";
import { joinTextParts, lastUserIndex, resendOf } from "@/lib/chat/chat-utils";
import type { ChatMessage } from "@/lib/chat/message";

/** The actions that rewrite the transcript of the chat being rendered. */
export function useChatActions() {
  const { chat } = useChatContext();

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
    }),
    [chat],
  );
}
