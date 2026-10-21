import { MarkdownText } from "@/components/chat/markdown/MarkdownText";
import { CompactionPart } from "@/components/chat/parts/CompactionPart";
import { ImagePart } from "@/components/chat/parts/ImagePart";
import { isReasoningRunning, ReasoningPart } from "@/components/chat/parts/ReasoningPart";
import { getToolHandler } from "@/components/chat/tools/registry";
import { SubagentTool } from "@/components/chat/tools/subagent";
import { ToolFallback } from "@/components/chat/tools/ToolFallback";
import { useSubagentLive } from "@/hooks/chat/use-subagent-live";
import { type ChatMessage, isOpenPrompt } from "@/lib/chat/chat-utils";
import { isSubagentTranscript } from "@/lib/chat/subagent";
import { getToolName, getToolPartInfo, type ToolPart } from "@/lib/chat/tool-part";

export type ChatPart = ChatMessage["parts"][number];

/**
 * Where a part of a reply goes: a step the agent took sits in a trace with its neighbors,
 * content and anything asking the user stands on its own, and the rest renders nothing.
 */
export function partLayout(part: ChatPart, streaming: boolean): "step" | "block" | null {
  switch (part.type) {
    case "text":
      return part.text.trim() ? "block" : null;
    case "reasoning":
      return part.text || isReasoningRunning(part, streaming) ? "step" : null;
    case "file":
      return part.mediaType.startsWith("image/") ? "block" : null;
    case "data-compaction":
      return "block";
  }

  if (!getToolName(part)) return null;

  return isOpenPrompt(part) ? "block" : "step";
}

interface MessagePartProps {
  part: ChatPart;
  toolData: ReadonlyMap<string, unknown>;
  reasoningDuration?: number;
}

/** One part of a reply that `partLayout` places. */
export function MessagePart({ part, toolData, reasoningDuration }: MessagePartProps) {
  switch (part.type) {
    case "text":
      return <MarkdownText>{part.text}</MarkdownText>;
    case "reasoning":
      return <ReasoningPart part={part} duration={reasoningDuration} />;
    // A tool that returns binary content lands here too (pydantic-ai extracts it into a trailing user message).
    case "file":
      return <ImagePart url={part.url} filename={part.filename} />;
    case "data-compaction":
      return <CompactionPart content={part.data.content} />;
    default:
      return <ToolMessagePart part={part} toolData={toolData} />;
  }
}

// The live-subagent subscription lives here, so only tool parts depend on the live map.
function ToolMessagePart({ part, toolData }: Omit<MessagePartProps, "reasoningDuration">) {
  const toolCallId = "toolCallId" in part ? part.toolCallId : undefined;
  const liveSubagent = useSubagentLive(toolCallId);
  const info = getToolPartInfo(part, toolData);

  if (!info) return null;

  const tool = part as ToolPart;
  // Any subagent tool renders its delegated run: the persisted transcript once it arrives, the live one before.
  const subagentSteps = isSubagentTranscript(info.metadata) ? info.metadata.steps : liveSubagent;

  if (subagentSteps)
    return <SubagentTool toolName={info.toolName} part={tool} steps={subagentSteps} />;

  const handler = getToolHandler(info.toolName);

  if (handler?.render) return handler.render({ part: tool, metadata: info.metadata });

  return <ToolFallback toolName={info.toolName} part={tool} />;
}
