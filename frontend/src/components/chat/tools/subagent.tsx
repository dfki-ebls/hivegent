import { BrainIcon, type LucideIcon, MessageSquareIcon, WrenchIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { ChainOfThoughtStep } from "@/components/ai-elements/chain-of-thought";
import { MarkdownText } from "@/components/chat/markdown/MarkdownText";
import { ToolCard } from "@/components/chat/tools/ToolCard";
import { ToolResult, ToolSection } from "@/components/ToolDisplay";
import type { SubagentStep } from "@/lib/chat/subagent";
import { prettyPrint, type ToolPart } from "@/lib/chat/tool-part";
import { snakeCaseToTitleCase } from "@/lib/utils";

function describeStep(step: SubagentStep): { icon: LucideIcon; label: string } {
  switch (step.kind) {
    case "reasoning":
      return { icon: BrainIcon, label: "Reasoning" };
    case "message":
      return { icon: MessageSquareIcon, label: "Response" };
    case "tool":
      return { icon: WrenchIcon, label: snakeCaseToTitleCase(step.tool_name ?? "tool") };
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
  const state: ToolPart["state"] = part.state ?? "output-available";
  const isRunning = state === "input-available" || state === "input-streaming";

  // Expand the card while the live run is working and collapse it once the
  // subagent returns, so its progress is visible without a manual click. A
  // loaded transcript mounts already completed, so it stays closed. The effect
  // only fires on a running <-> done transition, so a manual toggle in between
  // is preserved.
  const [open, setOpen] = useState(isRunning);

  useEffect(() => {
    setOpen(isRunning);
  }, [isRunning]);

  return (
    <ToolCard toolName={toolName} part={part} open={open} onOpenChange={setOpen}>
      {(steps.length > 0 || isRunning) && (
        <ToolSection title="Steps" border>
          {steps.length > 0 ? (
            <div className="space-y-3">
              {steps.map((step, index) => {
                const { icon, label } = describeStep(step);
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
            <p className="text-muted-foreground animate-pulse">Working…</p>
          )}
        </ToolSection>
      )}
      {part.output !== undefined && (
        <ToolResult>
          {typeof part.output === "string" ? (
            <MarkdownText>{part.output}</MarkdownText>
          ) : (
            <pre className="whitespace-pre-wrap text-xs font-mono">{prettyPrint(part.output)}</pre>
          )}
        </ToolResult>
      )}
    </ToolCard>
  );
}
