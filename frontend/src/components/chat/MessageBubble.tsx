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

  return (
    <Message from={message.role}>
      <MessageContent>
        {parts.map((part, partIndex) => {
          const isLastTextPart =
            part.type === "text" &&
            isAssistant &&
            isLastMessage &&
            !parts.slice(partIndex + 1).some((p) => p.type === "text");

          return (
            <MessagePart
              key={partIndex}
              toolData={toolData}
              part={part}
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
