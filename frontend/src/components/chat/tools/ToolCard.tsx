import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { TraceStepCollapsible, type TraceStepStatus } from "@/components/chat/TraceStep";
import { ApprovalDecision, ApprovalPrompt } from "@/components/chat/tools/approval";
import { ChangesetSummarySchema, ChangesetView } from "@/components/chat/tools/changeset";
import { ToolError, ToolParameters } from "@/components/chat/tools/display";
import { ToolIcon } from "@/components/chat/tools/icons";
import { useMessageStreaming } from "@/hooks/chat/use-message-streaming";
import { useApprovalMetadata } from "@/hooks/chat/use-tool-approval";
import type { chat } from "@/i18n/locales/en/chat";
import { type ToolPart, toolDisplayName, toolInput } from "@/lib/chat/tool-part";

interface LineState {
  status?: TraceStepStatus;
  label?: keyof typeof chat.tools.status;
}

const RUNNING: LineState = { status: "running", label: "running" };

/** How a call's line reads, where a call that never finished was stopped once the chat stops streaming. */
export function toolLineState(part: ToolPart, streaming: boolean): LineState {
  switch (part.state) {
    case "input-streaming":
    case "input-available":
      return streaming ? RUNNING : { status: "error", label: "stopped" };
    case "approval-requested":
      return { label: "awaitingApproval" };
    case "approval-responded":
      if (!part.approval.approved) return { label: "denied" };

      return streaming ? RUNNING : { label: "approved" };
    case "output-available":
      return part.preliminary && streaming ? RUNNING : {};
    case "output-error":
      return { status: "error", label: "failed" };
    case "output-denied":
      return { label: "denied" };
  }
}

interface ToolCardProps {
  toolName: string;
  part: ToolPart;
  /** Override the title, which defaults to the tool's display name. */
  title?: string;
  /** Present the call's input, which defaults to the raw arguments. */
  parameters?: ReactNode;
  /** Present the call's output. */
  children?: ReactNode;
  /** Whether the line is open until the user toggles it, such as while a subagent works. */
  autoOpen?: boolean;
}

/**
 * One tool call as a trace line that expands to its input, the user's decision, and its output.
 * A call that waits for approval asks with its input instead, along with the changeset
 * summary that every call changing the workspace sends as approval metadata.
 */
export function ToolCard({
  toolName,
  part,
  title,
  parameters,
  children,
  autoOpen = false,
}: ToolCardProps) {
  const { t } = useTranslation();
  const streaming = useMessageStreaming();
  const summary = useApprovalMetadata(part, ChangesetSummarySchema);
  const input = toolInput<Record<string, unknown>>(part);
  const name = title ?? toolDisplayName(t, toolName);
  const icon = <ToolIcon name={toolName} />;
  const details = parameters ?? (input && <ToolParameters params={input} />);

  if (part.state === "approval-requested") {
    return (
      <ApprovalPrompt
        icon={icon}
        toolName={toolName}
        title={name}
        approvalId={part.approval.id}
        summary={summary}
      >
        {details}
      </ApprovalPrompt>
    );
  }

  const { status, label } = toolLineState(part, streaming);

  return (
    <TraceStepCollapsible
      icon={icon}
      title={name}
      status={status}
      description={label && t(($) => $.chat.tools.status[label])}
      autoOpen={autoOpen}
      contentClassName="flex flex-col gap-3"
    >
      {part.state !== "input-streaming" && (
        <>
          {details}
          {part.approval?.approved !== undefined && <ApprovalDecision approval={part.approval} />}
          {summary && <ChangesetView summary={summary} />}
          {children}
          {part.state === "output-error" && <ToolError message={part.errorText} />}
        </>
      )}
    </TraceStepCollapsible>
  );
}
