import { MarkdownText } from "@/components/chat/markdown/markdown-text";
import { CompactionPart } from "@/components/chat/parts/compaction-part";
import { ImagePart } from "@/components/chat/parts/image-part";
import { ReasoningPart } from "@/components/chat/parts/reasoning-part";
import { getToolHandler } from "@/components/chat/tools/registry";
import { ToolFallback } from "@/components/chat/tools/tool-fallback";
import type { ChatPart } from "@/lib/chat/layout";
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
    // A file the model generated itself (pydantic-ai's `FilePart`), since a tool's binary output stays in its tool part.
    case "file":
      return <ImagePart url={part.url} filename={part.filename} />;
    case "data-compaction":
      return <CompactionPart content={part.data.content} />;
    default:
      return <ToolMessagePart part={part} toolData={toolData} prompt={prompt} />;
  }
}

function ToolMessagePart({ part, toolData, prompt }: Omit<MessagePartProps, "reasoningDuration">) {
  const info = getToolPartInfo(part, toolData);

  if (!info) return null;

  const tool = part as ToolPart;
  const handler = getToolHandler(info.toolName);

  if (handler?.render) return handler.render({ part: tool, metadata: info.metadata, prompt });

  return <ToolFallback toolName={info.toolName} part={tool} prompt={prompt} />;
}
