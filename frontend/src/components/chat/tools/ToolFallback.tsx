import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolOutputResult } from "@/components/chat/tools/display";
import type { ToolPart } from "@/lib/chat/tool-part";

/** A call of a tool without a view of its own, with its raw arguments and output. */
export function ToolFallback({ toolName, part }: { toolName: string; part: ToolPart }) {
  return (
    <ToolCard toolName={toolName} part={part}>
      <ToolOutputResult part={part} />
    </ToolCard>
  );
}
