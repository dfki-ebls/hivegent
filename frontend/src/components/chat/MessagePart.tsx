import type { UIMessage } from "@ai-sdk/react";
import { ImagePart } from "@/components/chat/parts/ImagePart";
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
  reasoningDuration?: number;
  isLastTextPart: boolean;
  showActions: boolean;
  isUserMessage: boolean;
  messageId: string;
  isEditing: boolean;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
  onRegenerate: () => void;
  onExecutePlan?: () => void;
}

export function MessagePart({
  toolData,
  part,
  reasoningDuration,
  isLastTextPart,
  showActions,
  isUserMessage,
  messageId,
  isEditing,
  onCancelEdit,
  onSubmitEdit,
  onRegenerate,
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
    return <ReasoningPart part={part} duration={reasoningDuration} />;
  }

  // A user attachment is always an image, but a tool that returns binary
  // content lands here too (pydantic-ai extracts it into a trailing user
  // message), so gate on the media type rather than the part kind.
  if (part.type === "file" && part.mediaType.startsWith("image/")) {
    return <ImagePart url={part.url} filename={part.filename} />;
  }

  if (part.type === "step-start") {
    return null;
  }

  return <ToolMessagePart toolData={toolData} part={part} onExecutePlan={onExecutePlan} />;
}

interface ToolMessagePartProps {
  toolData: ReadonlyMap<string, unknown>;
  part: UIMessage["parts"][number];
  onExecutePlan?: () => void;
}

// Tool parts only: the live-subagent context subscription lives here rather
// than in MessagePart, so only tool parts depend on the live map (other part
// types never subscribe to it).
function ToolMessagePart({ toolData, part, onExecutePlan }: ToolMessagePartProps) {
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
    <ToolFallback toolName={info.toolName} part={part as ToolPart} formatted={info.formatted} />
  );
}
