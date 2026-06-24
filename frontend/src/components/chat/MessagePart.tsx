import type { UIMessage } from "@ai-sdk/react";
import { ReasoningPart } from "@/components/chat/parts/ReasoningPart";
import { TextPart } from "@/components/chat/parts/TextPart";
import { UserTextPart } from "@/components/chat/parts/UserTextPart";
import { getToolHandler } from "@/components/chat/tools/registry";
import { SubagentTool } from "@/components/chat/tools/subagent";
import { ToolFallback } from "@/components/chat/tools/ToolFallback";
import { useSubagentLive } from "@/hooks/chat/use-subagent-live";
import { isSubagentTranscript } from "@/lib/chat/subagent";
import { getToolPartInfo, type ToolPart } from "@/lib/chat/tool-part";

interface MessagePartProps {
  toolData: ReadonlyMap<string, unknown>;
  part: UIMessage["parts"][number];
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
  toolData,
  part,
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

  return (
    <ToolMessagePart
      toolData={toolData}
      part={part}
      onApprove={onApprove}
      onDeny={onDeny}
      onExecutePlan={onExecutePlan}
    />
  );
}

interface ToolMessagePartProps {
  toolData: ReadonlyMap<string, unknown>;
  part: UIMessage["parts"][number];
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
  onExecutePlan?: () => void;
}

// Tool parts only: the live-subagent context subscription lives here rather
// than in MessagePart, so only tool parts depend on the live map (other part
// types never subscribe to it).
function ToolMessagePart({
  toolData,
  part,
  onApprove,
  onDeny,
  onExecutePlan,
}: ToolMessagePartProps) {
  const toolCallId = "toolCallId" in part ? (part.toolCallId as string) : undefined;
  const liveSubagent = useSubagentLive(toolCallId);

  const info = getToolPartInfo(part, toolData);
  if (!info) return null;

  // Any subagent tool (explore today, others later) renders its delegated run's
  // transcript: the persisted one once it arrives and after reload, the live
  // stream while it is still running.
  const subagentSteps = isSubagentTranscript(info.metadata) ? info.metadata.steps : liveSubagent;
  if (subagentSteps) {
    return <SubagentTool toolName={info.toolName} part={part as ToolPart} steps={subagentSteps} />;
  }

  const handler = getToolHandler(info.toolName);
  if (handler?.render) {
    return handler.render({
      part: part as ToolPart,
      metadata: info.metadata,
      onExecutePlan,
    });
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
