import type { ToolUIPart } from "ai";
import { ApprovalRequest } from "@/components/chat/tools/ApprovalRequest";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolResult } from "@/components/ToolDisplay";
import { prettyPrint, type ToolPart } from "@/lib/chat/tool-part";

interface ToolFallbackProps {
  toolName: string;
  part: ToolPart;
  formatted?: string | null;
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
}

export function ToolFallback({ toolName, part, formatted, onApprove, onDeny }: ToolFallbackProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const approval = "approval" in part ? (part as ToolUIPart).approval : undefined;

  return (
    <ToolCard toolName={toolName} part={part} defaultOpen={state === "approval-requested"}>
      {approval && (
        <ApprovalRequest
          toolName={toolName}
          approval={approval}
          state={state}
          onApprove={onApprove}
          onDeny={onDeny}
        />
      )}
      {part.output !== undefined && (
        <ToolResult>
          <pre className="whitespace-pre-wrap text-xs font-mono">
            {formatted ?? prettyPrint(part.output)}
          </pre>
        </ToolResult>
      )}
    </ToolCard>
  );
}
