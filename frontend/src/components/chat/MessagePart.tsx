import type { UIMessage } from "@ai-sdk/react";
import { CopyIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MessageAction, MessageActions } from "@/components/ai-elements/message";
import { MarkdownText } from "@/components/chat/markdown/MarkdownText";
import { ImagePart } from "@/components/chat/parts/ImagePart";
import { ReasoningPart } from "@/components/chat/parts/ReasoningPart";
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
  isUserMessage: boolean;
  messageId: string;
  isEditing: boolean;
  canCopy: boolean;
  onCancelEdit: () => void;
  onSubmitEdit: (messageId: string, newText: string) => void;
  onExecutePlan?: () => void;
}

export function MessagePart({
  toolData,
  part,
  reasoningDuration,
  isUserMessage,
  messageId,
  isEditing,
  canCopy,
  onCancelEdit,
  onSubmitEdit,
  onExecutePlan,
}: MessagePartProps) {
  const { t } = useTranslation();

  if (part.type === "text") {
    return (
      <div className="flex flex-col gap-1.5">
        {isUserMessage ? (
          <UserTextPart
            text={part.text}
            messageId={messageId}
            isEditing={isEditing}
            onCancelEdit={onCancelEdit}
            onSubmitEdit={onSubmitEdit}
          />
        ) : (
          <MarkdownText>{part.text}</MarkdownText>
        )}
        {canCopy && (
          <MessageActions className={isUserMessage ? "ml-auto" : undefined}>
            <MessageAction
              onClick={() => void navigator.clipboard.writeText(part.text)}
              label={t(($) => $.common.actions.copy)}
            >
              <CopyIcon className="size-3" />
            </MessageAction>
          </MessageActions>
        )}
      </div>
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
