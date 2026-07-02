import type { UIMessage } from "@ai-sdk/react";
import type { ChatStatus } from "ai";
import { CopyIcon, PencilIcon } from "lucide-react";
import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
} from "@/components/ai-elements/message";
import { MessagePart } from "@/components/chat/MessagePart";
import { joinTextParts } from "@/lib/chat/chat-utils";
import { indexToolData } from "@/lib/chat/tool-part";

const MS_IN_S = 1000;

interface ReasoningMetadata {
  reasoningDurationsMs?: number[];
}

function reasoningDurationSeconds(
  metadata: UIMessage["metadata"],
  reasoningIndex: number,
): number | undefined {
  const durationMs = (metadata as ReasoningMetadata | undefined)?.reasoningDurationsMs?.[
    reasoningIndex
  ];

  return typeof durationMs === "number" ? Math.ceil(durationMs / MS_IN_S) : undefined;
}

interface MessageBubbleProps {
  message: UIMessage;
  isLastMessage: boolean;
  status: ChatStatus;
  editingId: string | null;
  onSetEditing: (id: string) => void;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
  onRegenerate: () => void;
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
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
  onApprove,
  onDeny,
  onExecutePlan,
}: MessageBubbleProps) {
  const isAssistant = message.role === "assistant";
  const isUser = message.role === "user";
  const showActions = isAssistant && isLastMessage && status === "ready";
  const canEdit = isUser && (status === "ready" || status === "error") && editingId !== message.id;
  const parts = message.parts ?? [];
  const toolData = indexToolData(parts);
  // The final answer's actions hang off the last text part of an assistant turn.
  const lastTextIndex = parts.findLastIndex((p) => p.type === "text");
  let reasoningIndex = 0;

  return (
    <Message from={message.role}>
      {/* Assistant content spans full width so tool cards don't shrink to a short line. */}
      <MessageContent className={isAssistant ? "w-full gap-1.5" : "gap-1.5"}>
        {parts.map((part, partIndex) => {
          const isLastTextPart = isAssistant && isLastMessage && partIndex === lastTextIndex;
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
              isLastTextPart={isLastTextPart}
              showActions={showActions}
              isUserMessage={isUser}
              messageId={message.id}
              isEditing={editingId === message.id}
              onCancelEdit={onCancelEdit}
              onSubmitEdit={onSubmitEdit}
              onRegenerate={onRegenerate}
              onApprove={onApprove}
              onDeny={onDeny}
              onExecutePlan={isAssistant && isLastMessage ? onExecutePlan : undefined}
            />
          );
        })}
      </MessageContent>
      {canEdit && (
        <MessageActions className="ml-auto">
          <MessageAction onClick={() => onSetEditing(message.id)} label="Edit">
            <PencilIcon className="size-3" />
          </MessageAction>
          <MessageAction
            onClick={() => {
              const text = joinTextParts(parts);
              if (text) void navigator.clipboard.writeText(text);
            }}
            label="Copy"
          >
            <CopyIcon className="size-3" />
          </MessageAction>
        </MessageActions>
      )}
    </Message>
  );
}
