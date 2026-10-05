import { useChat } from "@ai-sdk/react";
import {
  type FileUIPart,
  DefaultChatTransport,
  lastAssistantMessageIsCompleteWithApprovalResponses,
} from "ai";
import type { BuildRequestBody } from "@/hooks/chat/use-build-request-body";
import type { ChatRequestConfig } from "@/lib/types";
import { useCallback, useMemo, useState } from "react";
import { getAuthHeaders } from "@/lib/api";
import {
  type ChatMessage,
  adoptMessageNodeId,
  declineAbandonedApprovals,
  isChatBusy,
  lastUserIndex,
} from "@/lib/chat/chat-utils";
import { API_BASE_URL } from "@/lib/health";
import type { SubagentSteps, SubagentUpdate } from "@/lib/chat/subagent";

/**
 * IDs the server issues in a turn's response headers. Read there rather than
 * off the stream so they survive a turn the user stops or that errors, since
 * the server persists the turn on every finish. One instance lives as long as
 * the hook, so the IDs outlast transport rebuilds.
 */
class TurnIds {
  /**
   * Conversation a draft was persisted under. Follow-up sends from the still
   * mounted draft (steering drain, approval auto-send) must target it, since
   * re-posting to the mint endpoint would create a duplicate.
   */
  adoptedConversationId: string | null = null;
  #conversationId: string | null = null;
  #messageId: string | null = null;

  capture(headers: Headers, draft: boolean): void {
    this.#messageId = headers.get("X-Message-Id");
    if (draft) this.#conversationId = headers.get("X-Conversation-Id");
  }

  /** Hand over the IDs of the finished turn, adopting a minted conversation. */
  take(): { conversationId: string | null; messageId: string | null } {
    const ids = { conversationId: this.#conversationId, messageId: this.#messageId };
    this.#conversationId = null;
    this.#messageId = null;
    if (ids.conversationId) this.adoptedConversationId = ids.conversationId;
    return ids;
  }
}

export interface SendUserMessageInput {
  text: string;
  files?: FileUIPart[];
  messageId?: string;
}

export interface UseHivegentChatOptions {
  /** When set, the first turn is sent to the id-less mint endpoint and the
   * server-issued ID is reported back via `onConversationCreated`. */
  draft?: boolean;
  onConversationCreated?: (id: string) => void;
  /** The current chat settings, read afresh for every outgoing request. */
  requestBody?: BuildRequestBody;
}

export function useHivegentChat(
  id: string,
  { draft, onConversationCreated, requestBody }: UseHivegentChatOptions = {},
) {
  const [turnIds] = useState(() => new TurnIds());
  // Adopted node ID to the message's first ID. Rendering keys on the first one,
  // since a remount on adoption makes the message scroller re-anchor.
  const [renderKeys] = useState(() => new Map<string, string>());

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: draft
          ? `${API_BASE_URL}/api/conversations/chat`
          : `${API_BASE_URL}/api/conversations/${id}/chat`,
        headers: () => getAuthHeaders(),
        // DB-first: the server owns history, so send only the new message
        // (none for a regenerate) plus the operation. The backend loads the
        // active-path prefix from its store and forks/appends under the node
        // addressed by `messageId`, ignoring the rest of the client array.
        //
        // The settings are stamped on here rather than at the call sites: the
        // SDK issues the post-approval continuation itself, with whatever
        // options the approval was recorded with, so a per-call body leaves
        // that request settingless and the turn resumes with the server's
        // defaults — no document scope, no model override, no MCP servers.
        // A per-call `body` still wins, for the one caller that overrides the
        // mode it is currently in.
        prepareSendMessagesRequest: ({ api, body, id: chatId, messages, trigger, messageId }) => {
          const lastMessage = trigger === "regenerate-message" ? undefined : messages.at(-1);
          return {
            api:
              draft && turnIds.adoptedConversationId
                ? `${API_BASE_URL}/api/conversations/${turnIds.adoptedConversationId}/chat`
                : api,
            body: {
              ...requestBody?.(),
              ...body,
              id: chatId,
              messages: lastMessage ? [lastMessage] : [],
              trigger,
              messageId,
            },
          };
        },
        // Every turn returns the node ID its user message is stored under, and
        // the first turn of a draft also returns the minted conversation ID;
        // capture both so the client can adopt them. Cross-origin reads require
        // the proxy to expose X-Message-Id and X-Conversation-Id via
        // Access-Control-Expose-Headers; same-origin (the default) needs none.
        fetch: async (input, init) => {
          const res = await fetch(input, init);
          turnIds.capture(res.headers, draft ?? false);
          return res;
        },
      }),
    [id, draft, requestBody, turnIds],
  );

  // Live subagent transcripts for the current conversation, keyed by parent
  // tool-call id. Built from transient `data-subagent` parts, which never reach
  // `message.parts`, so this is the only live source; a fresh map per event
  // changes the reference so context consumers re-render.
  const [subagentSteps, setSubagentSteps] = useState<SubagentSteps>(() => new Map());
  const [stepsId, setStepsId] = useState(id);

  if (stepsId !== id) {
    setStepsId(id);
    setSubagentSteps(new Map());
  }

  const chat = useChat<ChatMessage>({
    id,
    transport,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
    onData: (dataPart) => {
      if (dataPart.type !== "data-subagent") return;

      const { tool_call_id, transcript } = dataPart.data as SubagentUpdate;
      setSubagentSteps((prev) => new Map(prev).set(tool_call_id, transcript.steps));
    },
    // `useChat` always invokes the latest callbacks, so these stay current.
    onFinish: () => {
      const { conversationId, messageId } = turnIds.take();
      // Swap the SDK's local ID for the node ID, so editing or retrying this
      // message forks the stored branch at it instead of appending to the end.
      if (messageId) {
        chat.setMessages((messages) => {
          const localId = messages[lastUserIndex(messages)]?.id;
          if (localId) renderKeys.set(messageId, renderKeys.get(localId) ?? localId);
          return adoptMessageNodeId(messages, messageId);
        });
      }
      // The server mirrors the turn to storage on every finish (clean,
      // errored, or stopped), so a minted ID always names a persisted
      // conversation — adopt it unconditionally.
      if (conversationId) onConversationCreated?.(conversationId);
    },
  });

  const { sendMessage, regenerate } = chat;

  const sendUserMessage = useCallback(
    async (input: SendUserMessageInput, body?: ChatRequestConfig) => {
      const headers = await getAuthHeaders();
      // One payload shape for every send. An edit/retry addresses a node with
      // `messageId`, but it still submits a whole user message, so it carries
      // the attachments too — the server forks a fresh message there rather
      // than reusing the stored one, so anything left out is gone.
      const payload = { text: input.text, files: input.files, messageId: input.messageId };
      await sendMessage(payload, { headers, body });
    },
    [sendMessage],
  );

  const regenerateTurn = useCallback(async () => {
    await regenerate({ headers: await getAuthHeaders() });
  }, [regenerate]);

  // An approval the user overtook with another message is dead: the next
  // request closes it as a denial server-side, so the transcript every consumer
  // reads says so too, instead of leaving live buttons on a settled decision.
  const messages = useMemo(() => declineAbandonedApprovals(chat.messages), [chat.messages]);

  const isStreaming = isChatBusy(chat.status);
  const messageKey = useCallback(
    (messageId: string) => renderKeys.get(messageId) ?? messageId,
    [renderKeys],
  );

  return {
    ...chat,
    messages,
    messageKey,
    sendUserMessage,
    regenerateTurn,
    isStreaming,
    subagentSteps,
  };
}
