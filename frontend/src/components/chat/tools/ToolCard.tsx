import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { WrenchIcon } from "lucide-react";
import { TraceCollapsible, TraceCollapsibleContent } from "@/components/ui/trace";
import { TraceStepTrigger, type TraceStepStatus } from "@/components/chat/TraceStep";
import { ApprovalRequest } from "@/components/chat/tools/ApprovalRequest";
import { ChangesetSummarySchema } from "@/components/chat/tools/changeset";
import { ToolError, ToolParameters } from "@/components/ToolDisplay";
import { useApprovalMetadata } from "@/hooks/chat/use-tool-approval";
import { type ToolPart, toolDisplayName, toolInput } from "@/lib/chat/tool-part";

const LINE_STATUS: Partial<Record<ToolPart["state"], TraceStepStatus>> = {
  "input-streaming": "pending",
  "input-available": "running",
  "approval-responded": "running",
  "output-error": "error",
};

interface ToolCardProps {
  toolName: string;
  part: ToolPart;
  /** Override the header title; defaults to the tool's display name. */
  title?: string;
  /** Present the call's input; defaults to the raw parameter list. */
  parameters?: ReactNode;
  children?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

/**
 * Shared tool-call line that expands to parameters, approval, and results.
 * A call that changes the workspace asks with its changeset summary as approval metadata.
 * Approval can arrive after mount, so the line opens when a decision is pending.
 * Pass `open` and `onOpenChange` to control tool-specific expansion.
 */
export function ToolCard({
  toolName,
  part,
  title,
  parameters,
  children,
  open,
  onOpenChange,
}: ToolCardProps) {
  const { t } = useTranslation();
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = toolInput<Record<string, unknown>>(part);
  const summary = useApprovalMetadata(part, ChangesetSummarySchema);

  const cardTitle = title ?? toolDisplayName(t, toolName);
  const awaitingApproval = state === "approval-requested";
  const [selfOpen, setSelfOpen] = useState(false);

  const handleOpenChange = (next: boolean) => {
    setSelfOpen(next);
    onOpenChange?.(next);
  };

  return (
    <TraceCollapsible open={open ?? (selfOpen || awaitingApproval)} onOpenChange={handleOpenChange}>
      <TraceStepTrigger
        icon={<WrenchIcon />}
        title={cardTitle}
        status={LINE_STATUS[state]}
        description={
          state === "output-available" ? undefined : t(($) => $.chat.tools.status[state])
        }
      />
      <TraceCollapsibleContent className="space-y-4 text-foreground">
        {parameters ?? (input && <ToolParameters params={input} />)}
        {part.approval && (
          <ApprovalRequest
            toolName={toolName}
            title={cardTitle}
            approval={part.approval}
            state={state}
            summary={summary}
          />
        )}
        {children}
        {state === "output-error" && part.errorText && <ToolError message={part.errorText} />}
      </TraceCollapsibleContent>
    </TraceCollapsible>
  );
}
