import { type UseChatHelpers, useChat } from "@ai-sdk/react";
import {
  type FileUIPart,
  DefaultChatTransport,
  lastAssistantMessageIsCompleteWithApprovalResponses,
} from "ai";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getAuthHeaders } from "@/lib/api";
import { type ChatMessage, adoptMessageNodeId } from "@/lib/chat/chat-utils";
import { API_BASE_URL } from "@/lib/health";
import type { SubagentSteps, SubagentUpdate } from "@/lib/chat/subagent";

/**
 * Abort an in-flight request when the component unmounts, if one is *active*.
 *
 * `useChat` does not stop its stream when the hook unmounts, so a teardown that
 * is not a router navigation (e.g. an error boundary tripping mid-stream) would
 * orphan the server run. This is the belt-and-suspenders net behind
 * `StreamingNavGuard`, which already stops the stream on every navigation and
 * tab-close path. A ref holds the latest values so the cleanup can stay
 * unmount-only (empty deps) without going stale or re-subscribing each render.
 */
function useStopOnUnmount(active: boolean, stop: () => unknown): void {
  const latest = useRef({ active, stop });
  latest.current = { active, stop };

  useEffect(
    () => () => {
      if (latest.current.active) void latest.current.stop();
    },
    [],
  );
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
  requestBody?: () => Record<string, unknown>;
}

export function useHivegentChat(
  id: string,
  { draft, onConversationCreated, requestBody }: UseHivegentChatOptions = {},
) {
  const onCreatedRef = useRef(onConversationCreated);
  onCreatedRef.current = onConversationCreated;
  // ID minted for the in-flight draft turn. The server persists the turn
  // whenever it has started responding — on success, error, or a stop — so
  // once a minted ID came back (in the response header) the conversation is
  // a real row. The ID is staged here and adopted on finish regardless of
  // outcome, so a failed first turn still becomes a navigable conversation
  // and its retry continues it instead of minting a duplicate.
  const mintedIdRef = useRef<string | null>(null);
  // Once a draft turn is adopted, follow-up sends from this still-mounted
  // instance (steering drain, approval auto-send) must target the adopted
  // conversation: re-posting to the mint endpoint would create a duplicate.
  const adoptedIdRef = useRef<string | null>(null);
  // Tree-node ID the server reserved for the user message of the in-flight
  // turn. Read off the response headers rather than the stream so it survives
  // a turn the user stops or that errors — both persist the message.
  const messageNodeIdRef = useRef<string | null>(null);
  // `onFinish` is a `useChat` argument, so it cannot close over the chat it
  // belongs to; it reaches the one setter it needs through this ref.
  const setMessagesRef = useRef<UseChatHelpers<ChatMessage>["setMessages"] | null>(null);
  // Read inside the transport, which is memoized on the chat identity alone, so
  // the settings stay current without rebuilding it on every keystroke.
  const requestBodyRef = useRef(requestBody);
  requestBodyRef.current = requestBody;

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
              draft && adoptedIdRef.current
                ? `${API_BASE_URL}/api/conversations/${adoptedIdRef.current}/chat`
                : api,
            body: {
              ...requestBodyRef.current?.(),
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
          messageNodeIdRef.current = res.headers.get("X-Message-Id");
          if (draft) mintedIdRef.current = res.headers.get("X-Conversation-Id");
          return res;
        },
      }),
    [id, draft],
  );

  // Live subagent transcripts for the current conversation, keyed by parent
  // tool-call id. Built from transient `data-subagent` parts, which never reach
  // `message.parts`, so this is the only live source; a fresh map per event
  // changes the reference so context consumers re-render.
  const [subagentSteps, setSubagentSteps] = useState<SubagentSteps>(() => new Map());

  useEffect(() => {
    setSubagentSteps(new Map());
  }, [id]);

  const chat = useChat<ChatMessage>({
    id,
    transport,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
    onData: (dataPart) => {
      if (dataPart.type !== "data-subagent") return;

      const { tool_call_id, transcript } = dataPart.data as SubagentUpdate;
      setSubagentSteps((prev) => new Map(prev).set(tool_call_id, transcript.steps));
    },
    onFinish: () => {
      const nodeId = messageNodeIdRef.current;
      messageNodeIdRef.current = null;
      // Swap the SDK's local ID for the node ID, so editing or retrying this
      // message forks the stored branch at it instead of appending to the end.
      if (nodeId) {
        setMessagesRef.current?.((messages) => adoptMessageNodeId(messages, nodeId));
      }

      const mintedId = mintedIdRef.current;
      mintedIdRef.current = null;
      // The server mirrors the turn to storage on every finish (clean,
      // errored, or stopped), so a minted ID always names a persisted
      // conversation — adopt it unconditionally.
      if (mintedId) {
        adoptedIdRef.current = mintedId;
        onCreatedRef.current?.(mintedId);
      }
    },
  });

  setMessagesRef.current = chat.setMessages;

  const { sendMessage, regenerate } = chat;

  const sendUserMessage = useCallback(
    async (input: SendUserMessageInput, body?: Record<string, unknown>) => {
      const headers = await getAuthHeaders();
      const payload = input.messageId
        ? { text: input.text, messageId: input.messageId }
        : { text: input.text, files: input.files };
      await sendMessage(payload, { headers, body });
    },
    [sendMessage],
  );

  const regenerateTurn = useCallback(async () => {
    await regenerate({ headers: await getAuthHeaders() });
  }, [regenerate]);

  const isStreaming = chat.status === "submitted" || chat.status === "streaming";

  // Belt-and-suspenders behind StreamingNavGuard: abort the run on a teardown
  // that isn't a navigation (e.g. an error boundary), so it never outlives its UI.
  useStopOnUnmount(isStreaming, chat.stop);

  return { ...chat, sendUserMessage, regenerateTurn, isStreaming, subagentSteps };
}
