import type { ToolUIPart } from "ai";
import { ApprovalRequest } from "@/components/chat/tools/ApprovalRequest";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolPre, ToolResult } from "@/components/ToolDisplay";
import { prettyPrint, type ToolPart } from "@/lib/chat/tool-part";

interface ToolFallbackProps {
  toolName: string;
  part: ToolPart;
  formatted?: string | null;
}

export function ToolFallback({ toolName, part, formatted }: ToolFallbackProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const approval = "approval" in part ? (part as ToolUIPart).approval : undefined;

  return (
    <ToolCard toolName={toolName} part={part}>
      {approval && <ApprovalRequest toolName={toolName} approval={approval} state={state} />}
      {part.output !== undefined && (
        <ToolResult>
          <ToolPre>{formatted ?? prettyPrint(part.output)}</ToolPre>
        </ToolResult>
      )}
    </ToolCard>
  );
}
