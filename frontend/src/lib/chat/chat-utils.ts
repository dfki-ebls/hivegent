import type { UIMessage } from "@ai-sdk/react";
import {
  type ChatStatus,
  type DynamicToolUIPart,
  type FileUIPart,
  type ToolUIPart,
  isToolUIPart,
} from "ai";
import { ASK_USER_PART, DISMISSED_ERROR } from "@/lib/chat/ask-user";

/** Whether a chat request is waiting for or receiving model output. */
export function isChatBusy(status: ChatStatus): boolean {
  return status === "submitted" || status === "streaming";
}

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
  const last = messages.at(-1)?.parts.at(-1);
  const streamingOutput =
    (last?.type === "text" || last?.type === "reasoning") && last.state === "streaming";

  return isChatBusy(status) && !streamingOutput;
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
export function lastUserIndex(messages: UIMessage[]): number {
  return messages.findLastIndex((message) => message.role === "user");
}

/**
 * A user turn in the form a resend needs: its node id plus every part that has
 * to travel again.
 *
 * The single shape both resend paths read (the error banner's retry and the
 * post-compaction retry), so neither can quietly carry less than the user sent.
 * Attachments are the part that used to be dropped: they live only in client
 * state until the turn succeeds, so a resend that omits them loses them for good.
 */
export interface UserTurn {
  id: string;
  text: string;
  files: FileUIPart[];
}

/**
 * The last user message as a re-sendable turn, or undefined if there is
 * nothing to resend.
 *
 * A message carrying only attachments is a turn like any other — the chat
 * accepts an image with no prose — so emptiness is judged on text *and* files.
 */
export function getLastUserMessage(messages: UIMessage[]): UserTurn | undefined {
  const last = messages[lastUserIndex(messages)];
  if (!last) return undefined;
  const text = joinTextParts(last.parts) ?? "";
  const files = last.parts.filter((part): part is FileUIPart => part.type === "file");
  return text || files.length > 0 ? { id: last.id, text, files } : undefined;
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
 * window, in which case compaction can recover. The backend classifies
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
  /** What each pending call's `ApprovalRequired` carried, keyed by tool call id. */
  approvalMetadata?: Record<string, unknown>;
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
 * so every error surface agrees on what failed.
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
 * Whether a part is an approval request nobody has answered yet.
 *
 * `approval` is carried by the tool parts alone, so the `in` check is what
 * narrows the part union before the state is read.
 */
type PendingApproval = Extract<DynamicToolUIPart | ToolUIPart, { state: "approval-requested" }>;

function isPendingApproval(part: ChatMessage["parts"][number]): part is PendingApproval {
  return "approval" in part && part.state === "approval-requested";
}

/** The ids of the requests in *message* nobody has answered yet. */
export function pendingApprovalIds(message: ChatMessage): string[] {
  return message.parts.filter(isPendingApproval).map((part) => part.approval.id);
}

type PendingQuestion = Extract<ToolUIPart, { state: "input-available" }>;

/** Whether a part is a question call nobody has answered yet. */
function isPendingQuestion(part: ChatMessage["parts"][number]): part is PendingQuestion {
  return part.type === ASK_USER_PART && "state" in part && part.state === "input-available";
}

/** The same part as the denial or dismissal it has become, matching a reloaded one. */
function closeCall(part: ChatMessage["parts"][number]): ChatMessage["parts"][number] {
  if (isPendingApproval(part)) {
    return { ...part, state: "output-denied", approval: { ...part.approval, approved: false } };
  }

  if (isPendingQuestion(part)) {
    const { state: _, ...call } = part;

    return { ...call, state: "output-error", errorText: DISMISSED_ERROR };
  }

  return part;
}

/**
 * Close the approval requests and questions the user walked away from by
 * sending another message, so they read as the denials and dismissals they
 * have become.
 *
 * The backend does the same to the stored history on the next request
 * (`abandon_pending_calls` in `backend/src/hivegent/server/vercel.py`): a
 * dangling call that a new prompt has overtaken can never be answered, so it is
 * closed. Deriving that here rather than recording a second decision keeps the
 * live tab showing what a reload already shows, and stops the card offering
 * buttons for a decision the server has made. Only the last message can hold a
 * live request — the run that asked is the end of the transcript until the
 * next turn appends to it — so anything before it is abandoned by construction.
 *
 * Returns *messages* itself when nothing dangles, allocating nothing, so the
 * common case re-renders nothing on a transcript that grows with every chunk.
 */
export function closeAbandonedCalls(messages: ChatMessage[]): ChatMessage[] {
  const isAbandoned = (message: ChatMessage, index: number) =>
    index < messages.length - 1 &&
    message.parts.some((part) => isPendingApproval(part) || isPendingQuestion(part));

  if (!messages.some(isAbandoned)) return messages;

  return messages.map((message, index) =>
    isAbandoned(message, index) ? { ...message, parts: message.parts.map(closeCall) } : message,
  );
}

/** Whether a call is done, or decided and waiting for the continuation to run it. */
function isSettled(part: ToolUIPart | DynamicToolUIPart): boolean {
  return (
    (part.state === "output-available" && part.preliminary !== true) ||
    part.state === "output-error" ||
    part.state === "output-denied" ||
    part.state === "approval-responded"
  );
}

/**
 * Whether the user's decisions or answers settled the last step, so the run continues.
 *
 * The SDK's `lastAssistantMessageIsCompleteWithApprovalResponses` extended to
 * answered questions. Its `lastAssistantMessageIsCompleteWithToolCalls` would
 * also fire for a stopped step of server tools, which pydantic-ai does not mark
 * `providerExecuted`, so something the user answered must be among them.
 */
export function shouldContinueRun({ messages }: { messages: ChatMessage[] }): boolean {
  const message = messages.at(-1);

  if (message?.role !== "assistant") return false;

  const step = message.parts.slice(message.parts.findLastIndex((p) => p.type === "step-start") + 1);
  const calls = step.filter(isToolUIPart);
  const answered = calls.some(
    (part) =>
      part.state === "approval-responded" ||
      (part.type === ASK_USER_PART && part.state !== "input-available"),
  );

  return answered && calls.every(isSettled);
}
