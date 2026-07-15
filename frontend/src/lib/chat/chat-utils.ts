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

/** Find the last user message and return its id + concatenated text. */
export function getLastUserMessage(
  messages: UIMessage[],
): { id: string; text: string } | undefined {
  const last = [...messages].reverse().find((m) => m.role === "user");
  if (!last) return undefined;
  const text = joinTextParts(last.parts);
  return text ? { id: last.id, text } : undefined;
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
export function isContextLengthError(error: Error | null | undefined): boolean {
  return error?.message.startsWith(CONTEXT_LENGTH_ERROR_PREFIX) ?? false;
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
 * A persisted run error to surface as a banner on reload, or undefined.
 *
 * A stream error is transient live state that reload would otherwise lose, so
 * the backend stores it on the last turn's message metadata (see
 * `record_turn_error` in `backend/src/hivegent/server/vercel.py`). Reading the
 * last message shows the banner only while the latest turn is the failed one, so
 * a later successful turn clears it. Context-window overflows are suppressed here
 * just as they are live, since auto-compaction owns that case.
 */
export function persistedChatError(messages: UIMessage[]): string | undefined {
  const error = (messages.at(-1)?.metadata as ChatMessageMetadata | undefined)?.chatError;
  if (!error || error.startsWith(CONTEXT_LENGTH_ERROR_PREFIX)) return undefined;
  return error;
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
