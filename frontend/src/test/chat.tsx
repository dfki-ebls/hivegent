import { Chat } from "@ai-sdk/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import type { ChatTransport } from "ai";
import type { ReactNode } from "react";

import { ChatProvider } from "@/components/chat/chat-context";
import type { ChatMessage } from "@/lib/chat/message";
import { createTestQueryClient } from "@/test/render";

/** A transport whose requests never answer, so a chat that sends one keeps streaming. */
const pending: ChatTransport<ChatMessage> = {
  sendMessages: () => new Promise(() => {}),
  reconnectToStream: () => Promise.resolve(null),
};

/** A chat that holds *messages* and sends nothing anywhere. */
export function createTestChat(messages: ChatMessage[] = []): Chat<ChatMessage> {
  return new Chat<ChatMessage>({ id: "test", messages, transport: pending });
}

/** Put *chat* into a request that never answers, which leaves its messages as they are. */
export async function startRun(chat: Chat<ChatMessage>): Promise<void> {
  await act(async () => {
    void chat.sendMessage(undefined);
    await Promise.resolve();
  });
}

/** Render *ui* inside a query cache and *chat*, of the conversation `c1` by default. */
export function renderWithChat(
  ui: ReactNode,
  chat = createTestChat(),
  conversationId: string | null = "c1",
) {
  const client = createTestQueryClient();
  const wrap = (node: ReactNode) => (
    <QueryClientProvider client={client}>
      <ChatProvider chat={chat} conversationId={conversationId}>
        {node}
      </ChatProvider>
    </QueryClientProvider>
  );
  const result = render(wrap(ui));

  return { ...result, chat, rerender: (next: ReactNode) => result.rerender(wrap(next)) };
}
