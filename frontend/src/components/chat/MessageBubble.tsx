import type { ChatStatus } from "ai";
import { CopyIcon, PencilIcon, RefreshCcwIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApprovalScope } from "@/components/chat/ApprovalScope";
import { MessagePart } from "@/components/chat/MessagePart";
import { Button } from "@/components/ui/button";
import { Message, MessageContent, MessageFooter } from "@/components/ui/message";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  type ChatMessage,
  isChatBusy,
  joinTextParts,
  pendingApprovalIds,
} from "@/lib/chat/chat-utils";
import { indexToolData } from "@/lib/chat/tool-part";

const MS_IN_S = 1000;

function reasoningDurationSeconds(
  metadata: ChatMessage["metadata"],
  reasoningIndex: number,
): number | undefined {
  const durationMs = metadata?.reasoningDurationsMs?.[reasoningIndex];

  return typeof durationMs === "number" ? Math.ceil(durationMs / MS_IN_S) : undefined;
}

interface MessageActionProps {
  label: string;
  onClick: () => void;
  children: ReactNode;
}

function MessageAction({ label, onClick, children }: MessageActionProps) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={<Button variant="ghost" size="icon-sm" aria-label={label} onClick={onClick} />}
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
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
}: MessageBubbleProps) {
  const { t } = useTranslation();
  const isAssistant = message.role === "assistant";
  const isUser = message.role === "user";
  const isStreaming = isLastMessage && isChatBusy(status);
  const canRetry = isAssistant && isLastMessage && status === "ready";
  const canEdit = isUser && (status === "ready" || status === "error") && editingId !== message.id;
  const parts = message.parts ?? [];
  const toolData = indexToolData(parts);
  const canCopy =
    parts.some((part) => part.type === "text" && part.text !== "") &&
    !isStreaming &&
    editingId !== message.id;
  let reasoningIndex = 0;

  return (
    <Message align={isUser ? "end" : "start"}>
      <MessageContent>
        {/* Only the last message holds requests that are still answerable. */}
        <ApprovalScope
          pending={isAssistant && isLastMessage ? pendingApprovalIds(message) : []}
          metadata={message.metadata?.approvalMetadata}
        >
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
                onCancelEdit={onCancelEdit}
                onSubmitEdit={onSubmitEdit}
              />
            );
          })}
        </ApprovalScope>
        {(canCopy || canEdit || canRetry) && (
          <MessageFooter>
            {canCopy && (
              <MessageAction
                onClick={() => void navigator.clipboard.writeText(joinTextParts(parts) ?? "")}
                label={t(($) => $.common.actions.copy)}
              >
                <CopyIcon />
              </MessageAction>
            )}
            {canEdit && (
              <MessageAction
                onClick={() => onSetEditing(message.id)}
                label={t(($) => $.common.actions.edit)}
              >
                <PencilIcon />
              </MessageAction>
            )}
            {canRetry && (
              <MessageAction onClick={onRegenerate} label={t(($) => $.common.actions.retry)}>
                <RefreshCcwIcon />
              </MessageAction>
            )}
          </MessageFooter>
        )}
      </MessageContent>
    </Message>
  );
}
