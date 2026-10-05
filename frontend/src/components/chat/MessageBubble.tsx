import type { ChatStatus } from "ai";
import { PencilIcon, RefreshCcwIcon } from "lucide-react";
import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
} from "@/components/ai-elements/message";
import { MessagePart } from "@/components/chat/MessagePart";
import { type ChatMessage, isChatBusy } from "@/lib/chat/chat-utils";
import { indexToolData } from "@/lib/chat/tool-part";

const MS_IN_S = 1000;

function reasoningDurationSeconds(
  metadata: ChatMessage["metadata"],
  reasoningIndex: number,
): number | undefined {
  const durationMs = metadata?.reasoningDurationsMs?.[reasoningIndex];

  return typeof durationMs === "number" ? Math.ceil(durationMs / MS_IN_S) : undefined;
}

interface MessageBubbleProps {
  message: ChatMessage;
  isLastMessage: boolean;
  status: ChatStatus;
  editingId: string | null;
  onSetEditing: (id: string) => void;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
  onRegenerate: () => void;
  onExecutePlan?: () => void;
}

export function MessageBubble({
  message,
  isLastMessage,
  status,
  editingId,
  onSetEditing,
  onCancelEdit,
  onSubmitEdit,
  onRegenerate,
  onExecutePlan,
}: MessageBubbleProps) {
  const isAssistant = message.role === "assistant";
  const isUser = message.role === "user";
  const isStreaming = isLastMessage && isChatBusy(status);
  const canRetry = isAssistant && isLastMessage && status === "ready";
  const canEdit = isUser && (status === "ready" || status === "error") && editingId !== message.id;
  const parts = message.parts ?? [];
  const toolData = indexToolData(parts);
  const canCopy = (isAssistant || isUser) && !isStreaming && editingId !== message.id;
  let reasoningIndex = 0;

  return (
    <Message from={message.role}>
      {/* Assistant content spans full width so tool cards don't shrink to a short line. */}
      <MessageContent className={isAssistant ? "w-full gap-1.5" : "gap-1.5"}>
        {parts.map((part, partIndex) => {
          const reasoningDuration =
            part.type === "reasoning"
              ? reasoningDurationSeconds(message.metadata, reasoningIndex++)
              : undefined;

          return (
            <MessagePart
              key={partIndex}
              toolData={toolData}
              part={part}
              reasoningDuration={reasoningDuration}
              isUserMessage={isUser}
              messageId={message.id}
              isEditing={editingId === message.id}
              canCopy={canCopy}
              onCancelEdit={onCancelEdit}
              onSubmitEdit={onSubmitEdit}
              onExecutePlan={isAssistant && isLastMessage ? onExecutePlan : undefined}
            />
          );
        })}
      </MessageContent>
      {(canEdit || canRetry) && (
        <MessageActions className={isUser ? "ml-auto" : undefined}>
          {canEdit && (
            <MessageAction onClick={() => onSetEditing(message.id)} label="Edit">
              <PencilIcon className="size-3" />
            </MessageAction>
          )}
          {canRetry && (
            <MessageAction onClick={onRegenerate} label="Retry">
              <RefreshCcwIcon className="size-3" />
            </MessageAction>
          )}
        </MessageActions>
      )}
    </Message>
  );
}
