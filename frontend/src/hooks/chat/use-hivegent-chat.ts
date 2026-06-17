import { useChat } from "@ai-sdk/react";
import {
  type FileUIPart,
  DefaultChatTransport,
  lastAssistantMessageIsCompleteWithApprovalResponses,
} from "ai";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL, getAuthHeaders } from "@/lib/api";
import type { SubagentSteps, SubagentUpdate } from "@/lib/chat/subagent";

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
}

export function useHivegentChat(
  id: string,
  { draft, onConversationCreated }: UseHivegentChatOptions = {},
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
        prepareSendMessagesRequest: ({
          api,
          body,
          id: chatId,
          messages,
          trigger,
          messageId,
        }) => {
          const lastMessage =
            trigger === "regenerate-message" ? undefined : messages.at(-1);
          return {
            api:
              draft && adoptedIdRef.current
                ? `${API_BASE_URL}/api/conversations/${adoptedIdRef.current}/chat`
                : api,
            body: {
              ...body,
              id: chatId,
              messages: lastMessage ? [lastMessage] : [],
              trigger,
              messageId,
            },
          };
        },
        // The server mints the conversation ID on the first turn and returns
        // it in a response header; capture it so the client can adopt it.
        // Cross-origin reads require the proxy to expose X-Conversation-Id via
        // Access-Control-Expose-Headers; same-origin (the default) needs none.
        fetch: draft
          ? async (input, init) => {
              const res = await fetch(input, init);
              mintedIdRef.current = res.headers.get("X-Conversation-Id");
              return res;
            }
          : undefined,
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

  const chat = useChat({
    id,
    transport,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
    onData: (dataPart) => {
      if (dataPart.type !== "data-subagent") return;

      const { tool_call_id, transcript } = dataPart.data as SubagentUpdate;
      setSubagentSteps((prev) => new Map(prev).set(tool_call_id, transcript.steps));
    },
    onFinish: () => {
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

  const { sendMessage, regenerate } = chat;

  const sendUserMessage = useCallback(
    async (input: SendUserMessageInput, body: Record<string, unknown>) => {
      const headers = await getAuthHeaders();
      const payload = input.messageId
        ? { text: input.text, messageId: input.messageId }
        : { text: input.text, files: input.files };
      await sendMessage(payload, { headers, body });
    },
    [sendMessage],
  );

  const regenerateWithBody = useCallback(
    async (body: Record<string, unknown>) => {
      const headers = await getAuthHeaders();
      await regenerate({ headers, body });
    },
    [regenerate],
  );

  const isStreaming = chat.status === "submitted" || chat.status === "streaming";

  return { ...chat, sendUserMessage, regenerateWithBody, isStreaming, subagentSteps };
}
