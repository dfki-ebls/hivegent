import { BrainIcon, MessageSquareIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { MarkdownText } from "@/components/chat/markdown/MarkdownText";
import { TraceStep } from "@/components/chat/TraceStep";
import { ToolCard, toolLineState } from "@/components/chat/tools/ToolCard";
import { ToolCode } from "@/components/chat/tools/display";
import { ToolIcon } from "@/components/chat/tools/icons";
import { Trace } from "@/components/ui/trace";
import { useMessageStreaming } from "@/hooks/chat/use-message-streaming";
import type { SubagentStep } from "@/lib/chat/subagent";
import { prettyPrint, type ToolPart, toolDisplayName } from "@/lib/chat/tool-part";

function describeStep(t: TFunction, step: SubagentStep): { icon: ReactNode; label: string } {
  switch (step.kind) {
    case "reasoning":
      return { icon: <BrainIcon />, label: t(($) => $.chat.tools.subagent.reasoning) };
    case "message":
      return { icon: <MessageSquareIcon />, label: t(($) => $.chat.tools.subagent.response) };
    case "tool":
      return {
        icon: <ToolIcon name={step.tool_name ?? ""} />,
        label: toolDisplayName(t, step.tool_name ?? "tool"),
      };
  }
}

interface SubagentToolProps {
  toolName: string;
  part: ToolPart;
  /** Resolved transcript: the persisted one if available, else the live one. */
  steps: SubagentStep[];
}

/**
 * Any subagent tool, with its delegated run as a nested trace of reasoning, messages,
 * and tool calls, and the run's result below it.
 * The line opens while the subagent works and closes once it returns, unless the user toggled it.
 */
export function SubagentTool({ toolName, part, steps }: SubagentToolProps) {
  const { t } = useTranslation();
  const running = toolLineState(part, useMessageStreaming()).status === "running";

  return (
    <ToolCard toolName={toolName} part={part} autoOpen={running}>
      {(steps.length > 0 || running) && (
        <Trace>
          {steps.map((step, index) => {
            const { icon, label } = describeStep(t, step);

            return (
              <TraceStep
                key={index}
                icon={icon}
                title={label}
                status={running && index === steps.length - 1 ? "running" : undefined}
              />
            );
          })}
          {steps.length === 0 && (
            <TraceStep
              icon={<BrainIcon />}
              title={t(($) => $.common.states.working)}
              status="running"
            />
          )}
        </Trace>
      )}
      {typeof part.output === "string" ? (
        <MarkdownText>{part.output}</MarkdownText>
      ) : (
        part.output !== undefined && <ToolCode code={prettyPrint(part.output)} language="json" />
      )}
    </ToolCard>
  );
}
