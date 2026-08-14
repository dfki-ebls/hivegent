import { type ComponentProps, type ReactNode, useEffect, useState } from "react";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";
import { ToolError, ToolParameters } from "@/components/ToolDisplay";
import { useStayScrolledOnToggle } from "@/hooks/chat/use-stay-scrolled-on-toggle";
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
 * Shared shell for a tool-call card: the collapsible scaffolding, the
 * title-cased header, the auto-rendered parameters and error sections, and the
 * stay-scrolled wiring every card needs. Tool-specific bodies (results,
 * previews, timelines) go in `children`, slotted between the parameters and the
 * error; the parameters and error render straight from `part` so they can never
 * drift between cards. Expansion is the shell's too: a card that pauses for
 * approval opens itself, since the decision the run is blocked on is inside it.
 * `defaultOpen` seeds the initial state and cannot express that — a card mounts
 * while its input still streams, long before the approval request arrives — and
 * only opening is forced, so the card stays wherever the user puts it
 * afterwards. Pass `open`/`onOpenChange` to drive expansion from a rule of your
 * own instead; the stay-scrolled handler is always composed in.
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
  const stayScrolled = useStayScrolledOnToggle();

  const awaitingApproval = state === "approval-requested";
  const [selfOpen, setSelfOpen] = useState(defaultOpen ?? awaitingApproval);

  useEffect(() => {
    if (awaitingApproval) setSelfOpen(true);
  }, [awaitingApproval]);

  const handleOpenChange = (next: boolean) => {
    stayScrolled(next);
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
