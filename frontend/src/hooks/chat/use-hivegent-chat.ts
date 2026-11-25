import { Chat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useState } from "react";
import { useChatInstance } from "@/components/chat/chat-context";
import { chatFetch } from "@/api/client";
import {
  chatUrl,
  getConversationMessages,
  parseMessages,
  turnUrl,
} from "@/api/conversations";
import { settleTurn, shouldContinueRun } from "@/lib/chat/chat-utils";
import { type ChatMessage, messageSchemas } from "@/lib/chat/message";
import { SubagentLive } from "@/lib/chat/subagent";
/**
 * The transport of a conversation, which sends only the new message (none for
 * a regenerate or a compaction) plus the operation, since the server loads the
 * history from its store and forks or appends at the message `messageId` addresses.
 *
 * The body is the SDK's own: a new user message names its documents in its
 * metadata, and the server runs everything else, a regenerate, the
 * post-approval continuation the SDK issues itself, and a compaction included,
 * with the user's preferences and the documents of the question it answers.
 * A per-call body only adds a flag to its own request, such as `compact`,
 * which addresses no message since it compacts the active path.
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

/**
 * The chat of one conversation, which starts from its stored *messages*.
 *
 * Every turn ends on its stored version, which replaces what streamed, so the
 * transcript is the one a reload shows, ids included.
 */
function createChat(
  id: string,
  messages: ChatMessage[],
  subagents: SubagentLive,
): Chat<ChatMessage> {
  // The stored version of the turn streaming now once it arrived, undefined when invalid.
  let stored: Promise<ChatMessage[] | undefined> | undefined;
  const reload = () => {
    const read = chat.messages;

    void getConversationMessages(id).then((loaded) => {
      // Unless the user moved on meanwhile.
      if (chat.messages === read) chat.messages = loaded;
    });
  };
  const chat: Chat<ChatMessage> = new Chat<ChatMessage>({
    id,
    messages,
    transport: transportOf(id, reload),
    // The server stores every user message under the id it was sent with.
    generateId: () => crypto.randomUUID(),
    // Streamed messages are validated like stored ones.
    messageMetadataSchema: messageSchemas.metadataSchema,
    dataPartSchemas: messageSchemas.dataSchemas,
    sendAutomaticallyWhen: shouldContinueRun,
    onData: (part) => {
      if (part.type === "data-subagent") subagents.update(part.data);

      if (part.type === "data-turn") {
        stored = parseMessages(part.data).catch(() => undefined);
      }
    },
    onFinish: ({ message, isAbort, isError }) => {
      const turn = stored;

      stored = undefined;

      // The server ends a stream without the turn when its token expires, so
      // it is followed again as a reload would, with a fresh token.
      if (turn === undefined) {
        if (!isAbort && !isError) {
          void getConversationMessages(id).then((loaded) => {
            chat.messages = loaded;

            return chat.resumeStream();
          });
        }

        return;
      }

      void turn.then((settled) => {
        const next = settled && settleTurn(chat.messages, settled, message.id);

        if (next) chat.messages = next;
        else reload();
      });
    },
  });

  return chat;
}

/** One chat per conversation, created once for the component keyed by its id. */
export function useHivegentChat(id: string, messages: ChatMessage[]) {
  const [subagents] = useState(() => new SubagentLive());
  const chat = useChatInstance(() => createChat(id, messages, subagents));

  return { chat, subagents };
}
