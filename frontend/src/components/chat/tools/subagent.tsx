import { BrainIcon, type LucideIcon, MessageSquareIcon, WrenchIcon } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { ChainOfThoughtStep } from "@/components/ai-elements/chain-of-thought";
import { MarkdownText } from "@/components/chat/markdown/MarkdownText";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolPre, ToolResult, ToolSection } from "@/components/ToolDisplay";
import type { SubagentStep } from "@/lib/chat/subagent";
import { prettyPrint, type ToolPart, toolDisplayName } from "@/lib/chat/tool-part";

function describeStep(t: TFunction, step: SubagentStep): { icon: LucideIcon; label: string } {
  switch (step.kind) {
    case "reasoning":
      return { icon: BrainIcon, label: t(($) => $.chat.tools.subagent.reasoning) };
    case "message":
      return { icon: MessageSquareIcon, label: t(($) => $.chat.tools.subagent.response) };
    case "tool":
      return { icon: WrenchIcon, label: toolDisplayName(t, step.tool_name ?? "tool") };
  }
}

interface SubagentToolProps {
  toolName: string;
  part: ToolPart;
  /** Resolved transcript: the persisted one if available, else the live one. */
  steps: SubagentStep[];
}

/**
 * Generic renderer for any subagent tool, showing its delegated run as a coarse
 * timeline of reasoning, messages, and tool calls. Tool-name agnostic: routed by
 * `MessagePart` whenever a tool carries a subagent transcript.
 */
export function SubagentTool({ toolName, part, steps }: SubagentToolProps) {
  const { t } = useTranslation();
  const state: ToolPart["state"] = part.state ?? "output-available";
  const isRunning = state === "input-available" || state === "input-streaming";

  // Expand the card while the live run is working and collapse it once the
  // subagent returns, so its progress is visible without a manual click. A
  // loaded transcript mounts already completed, so it stays closed. Only a
  // running <-> done transition resets it, so a manual toggle in between is
  // preserved.
  const [open, setOpen] = useState(isRunning);
  const [wasRunning, setWasRunning] = useState(isRunning);

  if (isRunning !== wasRunning) {
    setWasRunning(isRunning);
    setOpen(isRunning);
  }

  return (
    <ToolCard toolName={toolName} part={part} open={open} onOpenChange={setOpen}>
      {(steps.length > 0 || isRunning) && (
        <ToolSection title={t(($) => $.chat.tools.sections.steps)} border>
          {steps.length > 0 ? (
            <div className="space-y-3">
              {steps.map((step, index) => {
                const { icon, label } = describeStep(t, step);
                const active = isRunning && index === steps.length - 1;
                return (
                  <ChainOfThoughtStep
                    key={index}
                    icon={icon}
                    label={label}
                    status={active ? "active" : "complete"}
                  />
                );
              })}
            </div>
          ) : (
            <p className="text-muted-foreground animate-pulse">
              {t(($) => $.common.states.working)}
            </p>
          )}
        </ToolSection>
      )}
      {part.output !== undefined && (
        <ToolResult>
          {typeof part.output === "string" ? (
            <MarkdownText>{part.output}</MarkdownText>
          ) : (
            <ToolPre>{prettyPrint(part.output)}</ToolPre>
          )}
        </ToolResult>
      )}
    </ToolCard>
  );
}
