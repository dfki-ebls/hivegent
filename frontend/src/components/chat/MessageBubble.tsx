import type { ChatStatus } from "ai";
import { CheckIcon, CopyIcon, PencilIcon, RefreshCcwIcon } from "lucide-react";
import { memo, type ReactNode, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApprovalScope } from "@/components/chat/ApprovalScope";
import { type ChatPart, MessagePart, partLayout } from "@/components/chat/MessagePart";
import { ImagePart } from "@/components/chat/parts/ImagePart";
import { UserTextPart } from "@/components/chat/parts/UserTextPart";
import { TooltipButton } from "@/components/TooltipButton";
import { AttachmentGroup } from "@/components/ui/attachment";
import { Message, MessageContent, MessageFooter } from "@/components/ui/message";
import { toast } from "@/components/ui/toast";
import { Trace } from "@/components/ui/trace";
import { MessageStreamingProvider } from "@/hooks/chat/use-message-streaming";
import {
  type ChatMessage,
  isChatBusy,
  joinTextParts,
  pendingApprovalIds,
} from "@/lib/chat/chat-utils";
import { indexToolData } from "@/lib/chat/tool-part";
import { stripVoidTags } from "@/lib/normalize-void-tags";

const MS_IN_S = 1000;

/** Copies the text *getText* builds on click, and confirms it with a check for two seconds. */
function CopyAction({ getText }: { getText: () => string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;

    const timeout = setTimeout(() => setCopied(false), 2000);

    return () => clearTimeout(timeout);
  }, [copied]);

  // Async, so a browser without the clipboard API outside a secure context rejects rather than throws.
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(getText());
      setCopied(true);
    } catch {
      toast.add({ type: "error", title: t(($) => $.common.states.copyFailed) });
    }
  };

  return (
    <TooltipButton label={t(($) => $.common.actions.copy)} onClick={() => void copy()}>
      {copied ? <CheckIcon /> : <CopyIcon />}
    </TooltipButton>
  );
}

/** A reply's text as markdown: its paragraphs between steps kept apart, its citation and image tags dropped. */
function replyMarkdown(message: ChatMessage): string {
  return message.parts
    .flatMap((part) => (part.type === "text" && part.text.trim() ? [stripVoidTags(part.text).trim()] : []))
    .join("\n\n");
}

interface IndexedPart {
  part: ChatPart;
  index: number;
}

type Block = { kind: "steps"; parts: IndexedPart[] } | ({ kind: "block" } & IndexedPart);

/** The parts of a reply, where each run of steps between two blocks forms one trace. */
function layoutBlocks(parts: ChatPart[], streaming: boolean): Block[] {
  const blocks: Block[] = [];

  parts.forEach((part, index) => {
    const layout = partLayout(part, streaming);
    const last = blocks.at(-1);

    if (layout === "block") {
      blocks.push({ kind: "block", part, index });
    } else if (layout === "step" && last?.kind === "steps") {
      last.parts.push({ part, index });
    } else if (layout === "step") {
      blocks.push({ kind: "steps", parts: [{ part, index }] });
    }
  });

  return blocks;
}

interface AssistantMessageProps {
  message: ChatMessage;
  isLastMessage: boolean;
  streaming: boolean;
  footer: ReactNode;
}

function AssistantMessage({ message, isLastMessage, streaming, footer }: AssistantMessageProps) {
  const toolData = indexToolData(message.parts);
  // The backend times each reasoning part in order, and the parts render in order.
  let reasoningRank = 0;
  const renderPart = ({ part, index }: IndexedPart) => {
    const durationMs =
      part.type === "reasoning"
        ? message.metadata?.reasoningDurationsMs?.[reasoningRank++]
        : undefined;

    return (
      <MessagePart
        key={index}
        part={part}
        toolData={toolData}
        reasoningDuration={durationMs === undefined ? undefined : Math.ceil(durationMs / MS_IN_S)}
      />
    );
  };

  return (
    <Message>
      <MessageContent>
        <MessageStreamingProvider value={streaming}>
          {/* Only the last message holds requests that are still answerable. */}
          <ApprovalScope
            pending={isLastMessage ? pendingApprovalIds(message) : []}
            metadata={message.metadata?.approvalMetadata}
          >
            {layoutBlocks(message.parts, streaming).map((block) =>
              block.kind === "steps" ? (
                <Trace key={block.parts[0].index}>{block.parts.map(renderPart)}</Trace>
              ) : (
                renderPart(block)
              ),
            )}
          </ApprovalScope>
        </MessageStreamingProvider>
        {footer}
      </MessageContent>
    </Message>
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

/**
 * One turn: the user's text in a bubble with its images, or the agent's reply with its steps.
 * Memoized, so a streamed chunk re-renders only the message it lands in.
 */
export const MessageBubble = memo(function MessageBubble({
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
  const streaming = isLastMessage && isChatBusy(status);
  const editing = editingId === message.id;
  const canCopy =
    !streaming && !editing && message.parts.some((part) => part.type === "text" && part.text.trim());

  if (message.role === "user") {
    const text = joinTextParts(message.parts) ?? "";
    const images = message.parts.flatMap((part) =>
      part.type === "file" && part.mediaType.startsWith("image/") ? [part] : [],
    );
    const canEdit = (status === "ready" || status === "error") && !editing;

    return (
      <Message align="end">
        <MessageContent>
          {images.length > 0 && (
            <AttachmentGroup className="flex-wrap justify-end">
              {images.map((image, index) => (
                <ImagePart key={index} url={image.url} filename={image.filename} />
              ))}
            </AttachmentGroup>
          )}
          {(text || editing) && (
            <UserTextPart
              text={text}
              isEditing={editing}
              onCancelEdit={onCancelEdit}
              onSubmitEdit={(newText) => onSubmitEdit(message.id, newText)}
            />
          )}
          {(canCopy || canEdit) && (
            <MessageFooter>
              {canCopy && <CopyAction getText={() => text} />}
              {canEdit && (
                <TooltipButton
                  label={t(($) => $.common.actions.edit)}
                  onClick={() => onSetEditing(message.id)}
                >
                  <PencilIcon />
                </TooltipButton>
              )}
            </MessageFooter>
          )}
        </MessageContent>
      </Message>
    );
  }

  const canRetry = isLastMessage && status === "ready";

  return (
    <AssistantMessage
      message={message}
      isLastMessage={isLastMessage}
      streaming={streaming}
      footer={
        (canCopy || canRetry) && (
          <MessageFooter className="px-0">
            {canCopy && <CopyAction getText={() => replyMarkdown(message)} />}
            {canRetry && (
              <TooltipButton label={t(($) => $.common.actions.retry)} onClick={onRegenerate}>
                <RefreshCcwIcon />
              </TooltipButton>
            )}
          </MessageFooter>
        )
      }
    />
  );
});
