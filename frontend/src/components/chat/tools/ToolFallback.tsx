import type { ToolUIPart } from "ai";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";
import { ApprovalRequest } from "@/components/chat/tools/ApprovalRequest";
import { ToolError, ToolParameters, ToolResult } from "@/components/ToolDisplay";
import { parseJson, prettyPrint, type ToolPart } from "@/lib/chat/tool-part";
import { snakeCaseToTitleCase } from "@/lib/utils";

interface ToolFallbackProps {
  toolName: string;
  part: ToolPart;
  formatted?: string | null;
  onApprove: (id: string) => void;
  onDeny: (id: string) => void;
}

export function ToolFallback({
  toolName,
  part,
  formatted,
  onApprove,
  onDeny,
}: ToolFallbackProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const approval = "approval" in part ? (part as ToolUIPart).approval : undefined;
  const input = parseJson<Record<string, unknown>>(part.input);

  return (
    <Tool defaultOpen={state === "approval-requested"}>
      <ToolHeader title={snakeCaseToTitleCase(toolName)} type={`tool-${toolName}`} state={state} />
      <ToolContent>
        {input && <ToolParameters params={input} />}
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
        {state === "output-error" && part.errorText && <ToolError message={part.errorText} />}
      </ToolContent>
    </Tool>
  );
}
