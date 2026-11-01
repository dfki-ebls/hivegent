import {
  type ChatStatus,
  type DynamicToolUIPart,
  type FileUIPart,
  type ToolUIPart,
  type UIMessage,
  isToolUIPart,
} from "ai";
import { ASK_USER_PART } from "@/lib/chat/ask-user";
import type { ChatPart } from "@/lib/chat/layout";
import type { ChatMessage } from "@/lib/chat/message";
import { type Live, toolLineState } from "@/lib/chat/tool-part";

/** Whether a chat request is waiting for or receiving model output. */
export function isChatBusy(status: ChatStatus): boolean {
  return status === "submitted" || status === "streaming";
}

/** Where the message `id` stands in a chat with `messages` and `status`. */
export function liveOf(messages: readonly ChatMessage[], status: ChatStatus, id: string): Live {
  if (messages.at(-1)?.id !== id) return "stale";

  return isChatBusy(status) ? "streaming" : "waiting";
}

/** Whether *part* visibly works on screen, as text that grows or a call whose line spins. */
function isPartRunning(part: ChatPart): boolean {
  if (isToolUIPart(part)) return toolLineState(part, "streaming").label === "running";

  return (part.type === "text" || part.type === "reasoning") && part.state === "streaming";
}

/**
 * Whether the turn shows a "processing" line, since nothing on screen says the model works.
 *
 * That is the wait for the first token and the gap after a step, where the
 * stream stays open with no visible output until the model starts its next block.
 */
export function isProcessing(messages: ChatMessage[], status: ChatStatus): boolean {
  const last = messages.at(-1)?.parts.at(-1);

  return isChatBusy(status) && !(last && isPartRunning(last));
}

/** Concatenate text from all text parts of a message (or part list). */
export function joinTextParts(parts: UIMessage["parts"] | undefined): string | undefined {
  if (!parts) return undefined;

  const texts = parts
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text);

  return texts.length > 0 ? texts.join("\n") : undefined;
}

/** The text of the last user message before the message *id*, empty when there is none. */
export function precedingUserText(messages: UIMessage[], id: string): string {
  const index = messages.findIndex((message) => message.id === id);
  const user = messages.findLast((message, at) => at < index && message.role === "user");

  return joinTextParts(user?.parts) ?? "";
}

/** Index of the message a turn sent, or -1. */
export function lastUserIndex(messages: UIMessage[]): number {
  return messages.findLastIndex((message) => message.role === "user");
}

/**
 * *message* sent again with *text*, replacing it and everything after it, with its attachments,
 * so a resend never quietly carries less than the user sent. The copy gets a fresh id,
 * since the server stores every message under the id it was sent with.
 */
export function resendOf(message: ChatMessage, text: string) {
  const files = message.parts.filter((part): part is FileUIPart => part.type === "file");

  return {
    id: crypto.randomUUID(),
    messageId: message.id,
    role: "user" as const,
    parts: [...files, ...(text ? [{ type: "text" as const, text }] : [])],
  };
}

/**
 * The run error of the latest turn, live or persisted, or undefined.
 *
 * A stream error is transient SDK state that a reload would lose, so the
 * backend stores it on the last turn's message metadata (see `record_turn_error`
 * in `backend/src/hivegent/server/vercel.py`). Reading the last message means a
 * later successful turn retires the error on its own.
 */
export function activeChatError(
  messages: ChatMessage[],
  liveError: Error | undefined,
): string | undefined {
  return liveError?.message ?? messages.at(-1)?.metadata?.chatError;
}

/**
 * Whether a part is an approval request nobody has answered yet.
 *
 * `approval` is carried by the tool parts alone, so the `in` check is what
 * narrows the part union before the state is read.
 */
type PendingApproval = Extract<DynamicToolUIPart | ToolUIPart, { state: "approval-requested" }>;

function isPendingApproval(part: ChatPart): part is PendingApproval {
  return "approval" in part && part.state === "approval-requested";
}

/** The ids of the requests in *message* nobody has answered yet. */
export function pendingApprovalIds(message: ChatMessage): string[] {
  return message.parts.filter(isPendingApproval).map((part) => part.approval.id);
}

/** Whether a part carries content, unlike the `step-start` marker between steps. */
function isContentPart(part: ChatPart): boolean {
  return part.type !== "step-start";
}

/**
 * Move a compaction that opens a reply to the end of the reply before its prompt.
 *
 * A compaction made for a prompt summarizes the history ahead of that prompt,
 * so the backend stores it there and a reload shows it at the end of the
 * previous reply. Live, it streams into the reply after the prompt instead. A
 * reply that held nothing else goes, as it does on reload.
 *
 * Returns *messages* itself when nothing moves, allocating nothing, so the
 * common case re-renders nothing on a transcript that grows with every chunk.
 */
export function placeCompactions(messages: ChatMessage[]): ChatMessage[] {
  let placed: ChatMessage[] | undefined;

  messages.forEach((message, index) => {
    const opening = message.parts.find(isContentPart);
    const previous = messages[index - 2];

    if (
      opening?.type !== "data-compaction" ||
      message.role !== "assistant" ||
      messages[index - 1]?.role !== "user" ||
      previous?.role !== "assistant"
    ) {
      return;
    }

    placed ??= [...messages];
    placed[index - 2] = { ...previous, parts: [...previous.parts, opening] };
    placed[index] = { ...message, parts: message.parts.filter((part) => part !== opening) };
  });

  return (
    placed?.filter(
      (message, index) => message === messages[index] || message.parts.some(isContentPart),
    ) ?? messages
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
