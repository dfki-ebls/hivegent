import type { UIMessage } from "@ai-sdk/react";
import type { ChatStatus } from "ai";

/**
 * Whether to show the standalone "thinking" loader below the conversation.
 *
 * The model is busy while the request is `submitted` (no output yet) or
 * `streaming`. A separate loader is redundant while text or reasoning streams,
 * since that content visibly grows on screen, so it is suppressed then. This
 * keeps the loader visible during the gap after a tool call, where the stream
 * stays open with no visible output until the model starts its next block.
 */
export function showThinkingLoader(messages: UIMessage[], status: ChatStatus): boolean {
  const busy = status === "submitted" || status === "streaming";
  const last = messages.at(-1)?.parts.at(-1);
  const streamingOutput =
    (last?.type === "text" || last?.type === "reasoning") && last.state === "streaming";

  return busy && !streamingOutput;
}

/** Concatenate text from all text parts of a message (or part list). */
export function joinTextParts(parts: UIMessage["parts"] | undefined): string | undefined {
  if (!parts) return undefined;
  const texts = parts
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text);
  return texts.length > 0 ? texts.join("\n") : undefined;
}

/**
 * Index of the message a turn sent, or -1.
 *
 * Retry addresses it by id and `adoptMessageNodeId` re-keys it, so both must
 * agree on which message that is.
 */
function lastUserIndex(messages: UIMessage[]): number {
  return messages.findLastIndex((message) => message.role === "user");
}

/** Find the last user message and return its id + concatenated text. */
export function getLastUserMessage(
  messages: UIMessage[],
): { id: string; text: string } | undefined {
  const last = messages[lastUserIndex(messages)];
  if (!last) return undefined;
  const text = joinTextParts(last.parts);
  return text ? { id: last.id, text } : undefined;
}

/**
 * Re-key the last user message to the tree-node id the backend stored it under
 * (returned in `X-Message-Id`), so edit and retry can address it.
 */
export function adoptMessageNodeId(messages: ChatMessage[], nodeId: string): ChatMessage[] {
  const index = lastUserIndex(messages);
  if (index === -1) return messages;
  return messages.with(index, { ...messages[index], id: nodeId });
}

/**
 * Stable prefix the backend puts onto context-window overflow errors in the
 * chat stream (see `chat_error_text` in `backend/src/hivegent/server/vercel.py`).
 */
const CONTEXT_LENGTH_ERROR_PREFIX = "context_length_exceeded: ";

/**
 * Whether a chat error means the conversation overflowed the model's context
 * window, in which case auto-compaction can recover. The backend classifies
 * provider errors and prefixes overflows with a stable code, so no matching
 * of provider-specific message text happens here.
 */
export function isContextLengthError(error: string | undefined): boolean {
  return error?.startsWith(CONTEXT_LENGTH_ERROR_PREFIX) ?? false;
}

/**
 * UI-owned metadata the backend persists on a message and the client reads back
 * on reload (see the `*_KEY` constants in `backend/src/hivegent/server/vercel.py`).
 */
export interface ChatMessageMetadata {
  reasoningDurationsMs?: number[];
  chatError?: string;
}

/**
 * The message type used throughout the chat, carrying our metadata shape.
 *
 * `UIMessage` defaults its metadata to `unknown`, which forces a cast at every
 * read; binding it once here types `message.metadata` everywhere instead. Chat
 * state is created from this type in `useChat` (see `use-hivegent-chat.ts`), so
 * the annotation flows outward rather than being re-asserted per call site.
 */
export type ChatMessage = UIMessage<ChatMessageMetadata>;

/**
 * The run error of the latest turn, live or persisted, or undefined.
 *
 * A stream error is transient SDK state that a reload or the draft-to-
 * conversation remount would lose, so the backend stores it on the last turn's
 * message metadata (see `record_turn_error` in
 * `backend/src/hivegent/server/vercel.py`) and `recordChatError` does the same
 * across the handoff. Reading the last message means a later successful turn
 * retires the error on its own. This is the single source every consumer reads,
 * so the banner and auto-compaction always agree on what failed.
 */
export function activeChatError(
  messages: ChatMessage[],
  liveError: Error | undefined,
): string | undefined {
  return liveError?.message ?? messages.at(-1)?.metadata?.chatError;
}

/**
 * Store a live run error on the last message so it survives the draft-to-
 * conversation handoff, which seeds the destination route from memory and so
 * skips the fetch that would return the copy the backend just persisted.
 */
export function recordChatError(messages: ChatMessage[], error: Error | undefined): ChatMessage[] {
  if (!error || messages.length === 0) return messages;

  const index = messages.length - 1;
  const message = messages[index];
  return messages.with(index, {
    ...message,
    metadata: { ...message.metadata, chatError: error.message },
  });
}

/**
 * Whether compacting the conversation could plausibly resolve an overflow.
 *
 * Compaction summarizes everything before the last user message and then
 * re-sends that message. It can only free up room when there is a prior user
 * turn to compress: a lone oversized turn (a huge pasted file, a request that
 * pulls in too much context) just overflows again, and a freshly compacted
 * conversation starts with only its summary plus one new turn. Requiring a
 * second user turn stops both from looping.
 */
export function canCompact(messages: UIMessage[]): boolean {
  let userTurns = 0;
  for (const message of messages) {
    if (message.role === "user" && ++userTurns >= 2) return true;
  }
  return false;
}
