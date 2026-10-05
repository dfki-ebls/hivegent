import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolPre, ToolResult } from "@/components/ToolDisplay";
import { prettyPrint, type ToolPart } from "@/lib/chat/tool-part";

interface ToolFallbackProps {
  toolName: string;
  part: ToolPart;
  formatted?: string | null;
}

export function ToolFallback({ toolName, part, formatted }: ToolFallbackProps) {
  return (
    <ToolCard toolName={toolName} part={part}>
      {part.output !== undefined && (
        <ToolResult>
          <ToolPre>{formatted ?? prettyPrint(part.output)}</ToolPre>
        </ToolResult>
      )}
    </ToolCard>
  );
}
