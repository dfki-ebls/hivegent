import { type ComponentProps, type ReactNode, useEffect, useState } from "react";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";
import { ToolError, ToolParameters } from "@/components/ToolDisplay";
import { parseJson, type ToolPart } from "@/lib/chat/tool-part";
import { snakeCaseToTitleCase } from "@/lib/utils";

type CollapsibleProps = Pick<ComponentProps<typeof Tool>, "open" | "defaultOpen" | "onOpenChange">;

interface ToolCardProps extends CollapsibleProps {
  toolName: string;
  part: ToolPart;
  /** Override the header title; defaults to the title-cased tool name. */
  title?: string;
  children?: ReactNode;
}

/**
 * Shared tool-call card for status, parameters, results, and approval expansion.
 * Approval can arrive after mount, so the card opens when a decision is pending.
 * Pass `open` and `onOpenChange` to control tool-specific expansion.
 */
export function ToolCard({
  toolName,
  part,
  title,
  children,
  open,
  defaultOpen,
  onOpenChange,
}: ToolCardProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = parseJson<Record<string, unknown>>(part.input);

  const awaitingApproval = state === "approval-requested";
  const [selfOpen, setSelfOpen] = useState(defaultOpen ?? awaitingApproval);

  useEffect(() => {
    if (awaitingApproval) setSelfOpen(true);
  }, [awaitingApproval]);

  const handleOpenChange = (next: boolean) => {
    setSelfOpen(next);
    onOpenChange?.(next);
  };

  return (
    <Tool open={open ?? selfOpen} className="mb-0" onOpenChange={handleOpenChange}>
      <ToolHeader
        title={title ?? snakeCaseToTitleCase(toolName)}
        type={`tool-${toolName}`}
        state={state}
      />
      <ToolContent>
        {input && <ToolParameters params={input} />}
        {children}
        {state === "output-error" && part.errorText && <ToolError message={part.errorText} />}
      </ToolContent>
    </Tool>
  );
}
