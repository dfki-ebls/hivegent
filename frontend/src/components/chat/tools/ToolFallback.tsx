import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolOutputResult } from "@/components/ToolDisplay";
import type { ToolPart } from "@/lib/chat/tool-part";

interface ToolFallbackProps {
  toolName: string;
  part: ToolPart;
  formatted?: string | null;
}

export function ToolFallback({ toolName, part, formatted }: ToolFallbackProps) {
  return (
    <ToolCard toolName={toolName} part={part}>
      <ToolOutputResult part={part} formatted={formatted} />
    </ToolCard>
  );
}
