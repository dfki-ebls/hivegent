import { BrainIcon, type LucideIcon, MessageSquareIcon, WrenchIcon } from "lucide-react";
import { ChainOfThoughtStep } from "@/components/ai-elements/chain-of-thought";
import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";
import { ToolParameters } from "@/components/ToolDisplay";
import { useStayScrolledOnToggle } from "@/hooks/chat/use-stay-scrolled-on-toggle";
import type { SubagentStep } from "@/lib/chat/subagent";
import { parseJson, type ToolPart } from "@/lib/chat/tool-part";
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
  const input = parseJson<Record<string, unknown>>(part.input);
  const isRunning = state === "input-available" || state === "input-streaming";
  const stayScrolled = useStayScrolledOnToggle();

  return (
    <Tool defaultOpen={isRunning} onOpenChange={stayScrolled}>
      <ToolHeader title={snakeCaseToTitleCase(toolName)} type={`tool-${toolName}`} state={state} />
      <ToolContent>
        {input && <ToolParameters params={input} />}
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
          isRunning && <p className="text-sm text-muted-foreground animate-pulse">Working…</p>
        )}
      </ToolContent>
    </Tool>
  );
}
