import type { ComponentProps, ReactNode } from "react";
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
 * drift between cards. Pass `open`/`defaultOpen`/`onOpenChange` to control
 * expansion; the stay-scrolled handler is always composed in.
 */
export function ToolCard({
  toolName,
  part,
  title,
  children,
  onOpenChange,
  ...openProps
}: ToolCardProps) {
  const state: ToolPart["state"] = part.state ?? "output-available";
  const input = parseJson<Record<string, unknown>>(part.input);
  const stayScrolled = useStayScrolledOnToggle();

  const handleOpenChange = (next: boolean) => {
    stayScrolled(next);
    onOpenChange?.(next);
  };

  return (
    <Tool {...openProps} className="mb-0" onOpenChange={handleOpenChange}>
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
