import { MarkdownText } from "@/components/chat/markdown/markdown-text";
import { CompactionPart } from "@/components/chat/parts/compaction-part";
import { ImagePart } from "@/components/chat/parts/image-part";
import { ReasoningPart } from "@/components/chat/parts/reasoning-part";
import { getToolHandler } from "@/components/chat/tools/registry";
import { SubagentTool } from "@/components/chat/tools/subagent";
import { ToolFallback } from "@/components/chat/tools/tool-fallback";
import { useSubagentLive } from "@/hooks/chat/use-subagent-live";
import type { ChatPart } from "@/lib/chat/layout";
import { isSubagentTranscript } from "@/lib/chat/subagent";
import { getToolPartInfo, type ToolPart } from "@/lib/chat/tool-part";

interface MessagePartProps {
  part: ChatPart;
  toolData: ReadonlyMap<string, unknown>;
  /** Whether a tool part asks the user something they can still answer. */
  prompt: boolean;
  reasoningDuration?: number;
}

/** One part of a reply that `blocksOf` places. */
export function MessagePart({ part, toolData, prompt, reasoningDuration }: MessagePartProps) {
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
      return <ToolMessagePart part={part} toolData={toolData} prompt={prompt} />;
  }
}

// The live-subagent subscription lives here, so only tool parts depend on the live map.
function ToolMessagePart({ part, toolData, prompt }: Omit<MessagePartProps, "reasoningDuration">) {
  const toolCallId = "toolCallId" in part ? part.toolCallId : undefined;
  const liveSubagent = useSubagentLive(toolCallId);
  const info = getToolPartInfo(part, toolData);

  if (!info) return null;

  const tool = part as ToolPart;
  // Any subagent tool renders its delegated run: the persisted transcript once it arrives, the live one before.
  const subagentSteps = isSubagentTranscript(info.metadata) ? info.metadata.steps : liveSubagent;

  if (subagentSteps)
    return (
      <SubagentTool toolName={info.toolName} part={tool} prompt={prompt} steps={subagentSteps} />
    );

  const handler = getToolHandler(info.toolName);

  if (handler?.render) return handler.render({ part: tool, metadata: info.metadata, prompt });

  return <ToolFallback toolName={info.toolName} part={tool} prompt={prompt} />;
}
