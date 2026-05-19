import type { UIMessage } from "@ai-sdk/react";
import { ReasoningPart } from "@/components/chat/parts/ReasoningPart";
import { TextPart } from "@/components/chat/parts/TextPart";
import { UserTextPart } from "@/components/chat/parts/UserTextPart";
import { getToolHandler } from "@/components/chat/tools/registry";
import { ToolFallback } from "@/components/chat/tools/ToolFallback";
import { getToolPartInfo, type ToolPart } from "@/lib/chat/tool-part";

interface MessagePartProps {
  parts: UIMessage["parts"];
  part: UIMessage["parts"][number];
  partIndex: number;
  isLastTextPart: boolean;
  showActions: boolean;
  isUserMessage: boolean;
  messageId: string;
  isEditing: boolean;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
  onRegenerate: () => void;
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
  onExecutePlan?: () => void;
}

export function MessagePart({
  parts,
  part,
  partIndex,
  isLastTextPart,
  showActions,
  isUserMessage,
  messageId,
  isEditing,
  onCancelEdit,
  onSubmitEdit,
  onRegenerate,
  onApprove,
  onDeny,
  onExecutePlan,
}: MessagePartProps) {
  if (part.type === "text" && isUserMessage) {
    return (
      <UserTextPart
        text={part.text}
        messageId={messageId}
        isEditing={isEditing}
        onCancelEdit={onCancelEdit}
        onSubmitEdit={onSubmitEdit}
      />
    );
  }

  if (part.type === "text") {
    return (
      <TextPart
        text={part.text}
        showActions={isLastTextPart && showActions}
        onRegenerate={onRegenerate}
      />
    );
  }

  if (part.type === "reasoning") {
    return <ReasoningPart part={part} />;
  }

  if (part.type === "step-start") {
    return null;
  }

  const info = getToolPartInfo(parts, partIndex);
  if (!info) return null;

  const handler = getToolHandler(info.toolName);
  if (handler?.render) {
    return handler.render({ part: part as ToolPart, onExecutePlan });
  }

  return (
    <ToolFallback
      toolName={info.toolName}
      part={part as ToolPart}
      formatted={info.formatted}
      onApprove={onApprove}
      onDeny={onDeny}
    />
  );
}
