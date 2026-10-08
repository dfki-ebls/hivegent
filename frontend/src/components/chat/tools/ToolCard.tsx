import { type ComponentProps, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CheckCircleIcon,
  ChevronDownIcon,
  CircleIcon,
  ClockIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { Tool, ToolContent } from "@/components/ai-elements/tool";
import { Badge } from "@/components/ui/badge";
import { CollapsibleTrigger } from "@/components/ui/collapsible";
import { ApprovalRequest } from "@/components/chat/tools/ApprovalRequest";
import { ChangesetSummarySchema } from "@/components/chat/tools/changeset";
import { ToolError, ToolParameters } from "@/components/ToolDisplay";
import { useApprovalMetadata } from "@/hooks/chat/use-tool-approval";
import { type ToolPart, toolDisplayName, toolInput } from "@/lib/chat/tool-part";

const STATUS_ICONS: Record<ToolPart["state"], ReactNode> = {
  "approval-requested": <ClockIcon className="size-4 text-yellow-600" />,
  "approval-responded": <CheckCircleIcon className="size-4 text-blue-600" />,
  "input-available": <ClockIcon className="size-4 animate-pulse" />,
  "input-streaming": <CircleIcon className="size-4" />,
  "output-available": <CheckCircleIcon className="size-4 text-green-600" />,
  "output-denied": <XCircleIcon className="size-4 text-orange-600" />,
  "output-error": <XCircleIcon className="size-4 text-red-600" />,
};

interface ToolCardHeaderProps {
  title: string;
  state: ToolPart["state"];
  /** Override the status label, for a tool whose states mean something else. */
  status?: string;
  icon?: ReactNode;
}

/** The AI Elements `ToolHeader` hardcodes English status labels, so the card renders its own. */
export function ToolCardHeader({ title, state, status, icon }: ToolCardHeaderProps) {
  const { t } = useTranslation();

  return (
    <CollapsibleTrigger className="flex w-full items-center justify-between gap-4 p-3">
      <div className="flex items-center gap-2">
        {icon ?? <WrenchIcon className="size-4 text-muted-foreground" />}
        <span className="font-medium text-sm">{title}</span>
        <Badge className="gap-1.5 rounded-full text-xs" variant="secondary">
          {STATUS_ICONS[state]}
          {status ?? t(($) => $.chat.tools.status[state])}
        </Badge>
      </div>
      <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
    </CollapsibleTrigger>
  );
}

type CollapsibleProps = Pick<ComponentProps<typeof Tool>, "open" | "onOpenChange">;

interface ToolCardProps extends CollapsibleProps {
  toolName: string;
  part: ToolPart;
  /** Override the header title; defaults to the tool's display name. */
  title?: string;
  /** Present the call's input; defaults to the raw parameter list. */
  parameters?: ReactNode;
  children?: ReactNode;
}

/**
 * Shared tool-call card for status, parameters, approval, and results.
 * A call that changes the workspace asks with its changeset summary as approval metadata.
 * Approval can arrive after mount, so the card opens when a decision is pending.
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
    <Tool
      open={open ?? (selfOpen || awaitingApproval)}
      className="mb-0"
      onOpenChange={handleOpenChange}
    >
      <ToolCardHeader title={cardTitle} state={state} />
      <ToolContent>
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
      </ToolContent>
    </Tool>
  );
}
