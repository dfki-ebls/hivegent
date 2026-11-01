import type { Chat } from "@ai-sdk/react";
import type { UIMessage } from "ai";
import {
  createContext,
  type ReactNode,
  use,
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import { isChatBusy, liveOf } from "@/lib/chat/chat-utils";
import type { ChatMessage } from "@/lib/chat/message";
import type { SubagentLive } from "@/lib/chat/subagent";
import type { Live } from "@/lib/chat/tool-part";

interface ChatContextValue {
  chat: Chat<ChatMessage>;
  /** The stored conversation whose messages and temporary files are shown, none for a chat that stores nothing. */
  conversationId: string | null;
  /** The live subagent transcripts of the chat, none for a chat that runs no subagents. */
  subagents?: SubagentLive;
}

/**
 * A chat created once for the component, which a key remounts for another one,
 * and stopped when it unmounts, since the SDK only stops a chat it created itself.
 */
export function useChatInstance<M extends UIMessage>(create: () => Chat<M>): Chat<M> {
  const [chat] = useState(create);

  useEffect(() => () => void chat.stop(), [chat]);

  return chat;
}

const ChatContext = createContext<ChatContextValue | null>(null);

interface ChatProviderProps extends ChatContextValue {
  children: ReactNode;
}

/** The chat the messages and their calls belong to, which stays the same while it streams. */
export function ChatProvider({ chat, conversationId, subagents, children }: ChatProviderProps) {
  const value = useMemo(
    () => ({ chat, conversationId, subagents }),
    [chat, conversationId, subagents],
  );

  return <ChatContext value={value}>{children}</ChatContext>;
}

export function useChatContext(): ChatContextValue {
  const value = use(ChatContext);

  if (!value) throw new Error("useChatContext must be used within a ChatProvider");

  return value;
}

/** The conversation being viewed, none outside of one. */
export function useConversationId(): string | null {
  return use(ChatContext)?.conversationId ?? null;
}

/**
 * A value derived from the chat's state, which renders again only once it changes,
 * unlike `useChat`, which renders again for every streamed chunk.
 * *select* must return a primitive or a stable reference.
 */
export function useChatSelector<T>(select: (chat: Chat<ChatMessage>) => T): T {
  const { chat } = useChatContext();
  // The callbacks `useChat` itself subscribes with, the Chat has no public ones.
  const subscribe = useCallback(
    (onChange: () => void) => {
      const unsubscribes = [
        chat["~registerMessagesCallback"](onChange),
        chat["~registerStatusCallback"](onChange),
        chat["~registerErrorCallback"](onChange),
      ];

      return () => unsubscribes.forEach((unsubscribe) => unsubscribe());
    },
    [chat],
  );

  return useSyncExternalStore(subscribe, () => select(chat));
}

/** Whether the chat waits for or receives model output. */
export function useChatBusy(): boolean {
  return useChatSelector((chat) => isChatBusy(chat.status));
}

/** Where `message` stands, which renders again only once that changes, not per streamed chunk. */
export function useLive(message: ChatMessage): Live {
  return useChatSelector((chat) => liveOf(chat.messages, chat.status, message.id));
}

const MessageLiveContext = createContext<Live>("stale");

/** Hands the live state of a message to the parts that render below it. */
export const MessageLiveProvider = MessageLiveContext.Provider;

/** Where the message being rendered stands, which decides whether a step still runs. */
export function useMessageLive(): Live {
  return use(MessageLiveContext);
}
