import type { ToolUIPart } from "ai";
import { type ComponentProps, type ReactNode, useState } from "react";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";
import { ApprovalRequest } from "@/components/chat/tools/ApprovalRequest";
import { ToolError, ToolParameters } from "@/components/ToolDisplay";
import { type ToolPart, toolInput } from "@/lib/chat/tool-part";
import { snakeCaseToTitleCase } from "@/lib/utils";

type CollapsibleProps = Pick<ComponentProps<typeof Tool>, "open" | "onOpenChange">;

interface ToolCardProps extends CollapsibleProps {
  toolName: string;
  part: ToolPart;
  /** Override the header title; defaults to the title-cased tool name. */
  title?: string;
  /** Present the call's input; defaults to the raw parameter list. */
  parameters?: ReactNode;
  /** What the call is about to do, in place of the generic approval question. */
  approvalPrompt?: ReactNode;
  children?: ReactNode;
}

/**
 * Shared tool-call card for status, parameters, approval, and results.
 * Approval can arrive after mount, so the card opens when a decision is pending.
 * Pass `open` and `onOpenChange` to control tool-specific expansion.
 */
export function ToolCard({
  toolName,
  part,
  title,
  parameters,
  approvalPrompt,
  children,
  open,
  onOpenChange,
}: ToolCardProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = toolInput<Record<string, unknown>>(part);
  const approval = "approval" in part ? (part as ToolUIPart).approval : undefined;

  const awaitingApproval = state === "approval-requested";
  const [selfOpen, setSelfOpen] = useState(false);

  const handleOpenChange = (next: boolean) => {
    setSelfOpen(next);
    onOpenChange?.(next);
  };

  return (
    <Tool
      open={open ?? (selfOpen || awaitingApproval)}
      className="mb-0"
      onOpenChange={handleOpenChange}
    >
      <ToolHeader
        title={title ?? snakeCaseToTitleCase(toolName)}
        type={`tool-${toolName}`}
        state={state}
      />
      <ToolContent>
        {parameters ?? (input && <ToolParameters params={input} />)}
        {approval && (
          <ApprovalRequest toolName={toolName} approval={approval} state={state}>
            {approvalPrompt}
          </ApprovalRequest>
        )}
        {children}
        {state === "output-error" && part.errorText && <ToolError message={part.errorText} />}
      </ToolContent>
    </Tool>
  );
}
