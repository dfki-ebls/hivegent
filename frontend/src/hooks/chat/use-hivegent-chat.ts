import { Chat } from "@ai-sdk/react";
import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { DefaultChatTransport } from "ai";
import { useState } from "react";
import { useChatInstance } from "@/components/chat/chat-context";
import { chatFetch } from "@/api/client";
import {
  type ChatRequestConfig,
  chatUrl,
  conversationQueries,
  getConversationMessages,
  turnUrl,
} from "@/api/conversations";
import { buildLlmConfig } from "@/api/settings";
import { buildToolsPayload } from "@/api/tools";
import { placeCompactions, shouldContinueRun } from "@/lib/chat/chat-utils";
import { type ChatMessage, messageSchemas } from "@/lib/chat/message";
import { SubagentLive } from "@/lib/chat/subagent";
import { useComposerStore } from "@/stores/composer-store";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { useSettingsStore } from "@/stores/settings-store";

/** The current chat settings, read afresh for every request. */
export function chatSettings(): ChatRequestConfig {
  const { overrides, personality, customSystemMessage, toolsSpec } = useSettingsStore.getState();
  const { included, excluded } = useDocumentFilterStore.getState();
  const { agentMode, reasoningEffort } = useComposerStore.getState();

  return {
    personality,
    system_message: personality === "custom" ? customSystemMessage : undefined,
    reasoning_effort: reasoningEffort,
    mode: agentMode,
    llm: buildLlmConfig(overrides),
    included_documents: included,
    excluded_documents: excluded,
    tools: buildToolsPayload(toolsSpec),
  };
}

/**
 * The transport of a conversation, which sends only the new message (none for
 * a regenerate or a compaction) plus the operation, since the server loads the
 * history from its store and forks or appends at the message `messageId` addresses.
 *
 * The settings are stamped on here rather than at the call sites: the SDK
 * issues the post-approval continuation itself, so a per-call body would leave
 * that request settingless and the turn would resume with the server's
 * defaults. A per-call body only adds a flag to its own request, such as
 * `compact`, which addresses no message since it compacts the active path.
 *
 * A turn runs on at the server when the client stops reading, so a reload
 * follows it again at its own route, from its first chunk. The replay
 * continues the message the stored history ends with, which the server ends
 * with what the turn answers or continues, so it is no replay of a whole message.
 * A turn that ended since the history was read is not followed, so
 * *onNoTurn* reads the history again.
 */
function transportOf(conversationId: string, onNoTurn: () => void) {
  const transport = new DefaultChatTransport<ChatMessage>({
    api: chatUrl(conversationId),
    fetch: chatFetch,
    resumeStreamIsReplay: false,
    prepareReconnectToStreamRequest: () => ({ api: turnUrl(conversationId) }),
    prepareSendMessagesRequest: ({ id, messages, body, trigger, messageId }) => {
      const compact = body?.compact === true;
      const last = compact || trigger === "regenerate-message" ? undefined : messages.at(-1);
      // An edit passes a whole message, whose `messageId` the SDK keeps on it.
      const message = last && {
        id: last.id,
        role: last.role,
        parts: last.parts,
        metadata: last.metadata,
      };

      return {
        body: {
          ...body,
          ...chatSettings(),
          id,
          messages: message ? [message] : [],
          trigger,
          messageId: compact ? undefined : messageId,
        },
      };
    },
  });
  const reconnect = transport.reconnectToStream.bind(transport);

  transport.reconnectToStream = async (options) => {
    const stream = await reconnect(options);

    if (stream === null) onNoTurn();

    return stream;
  };

  return transport;
}

/** The chat of one conversation, which starts from its stored *messages*. */
function createChat(
  id: string,
  messages: ChatMessage[],
  queryClient: QueryClient,
  subagents: SubagentLive,
): Chat<ChatMessage> {
  const chat: Chat<ChatMessage> = new Chat<ChatMessage>({
    id,
    messages,
    transport: transportOf(id, () => {
      const read = chat.messages;

      void getConversationMessages(id).then((stored) => {
        // Unless the user moved on meanwhile.
        if (chat.messages === read) chat.messages = stored;
      });
    }),
    // The server stores every user message under the id it was sent with.
    generateId: () => crypto.randomUUID(),
    // Streamed messages are validated like stored ones.
    messageMetadataSchema: messageSchemas.metadataSchema,
    dataPartSchemas: messageSchemas.dataSchemas,
    sendAutomaticallyWhen: shouldContinueRun,
    onData: (part) => {
      if (part.type === "data-subagent") subagents.update(part.data);
    },
    onFinish: () => {
      // Keep a compaction where it shows, so a regenerate dropping its reply keeps it too.
      const placed = placeCompactions(chat.messages);

      if (placed !== chat.messages) chat.messages = placed;

      // A turn creates the conversation, names it, and moves it to the top.
      void queryClient.invalidateQueries({ queryKey: conversationQueries.list().queryKey });
    },
  });

  return chat;
}

/** One chat per conversation, created once for the component keyed by its id. */
export function useHivegentChat(id: string, messages: ChatMessage[]) {
  const queryClient = useQueryClient();
  const [subagents] = useState(() => new SubagentLive());
  const chat = useChatInstance(() => createChat(id, messages, queryClient, subagents));

  return { chat, subagents };
}
