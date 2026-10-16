import { isToolUIPart } from "ai";
import { CircleAlertIcon, CircleCheckIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ChatPart } from "@/components/chat/MessagePart";
import { TraceStepCollapsible } from "@/components/chat/TraceStep";
import { toolLineState } from "@/components/chat/tools/ToolCard";
import { Spinner } from "@/components/ui/spinner";
import { Trace } from "@/components/ui/trace";
import { useMessageStreaming } from "@/hooks/chat/use-message-streaming";

const STATUS_ICONS = { running: <Spinner />, error: <CircleAlertIcon /> };

interface ChainOfThoughtProps {
  steps: ChatPart[];
  /** Whether the agent still works on these steps, which nothing follows yet. */
  running: boolean;
  /** The rendered steps. */
  children: ReactNode;
}

/**
 * A run of steps as one summary line that opens to them.
 * It opens while the agent works and closes once the reply moves on, unless the user toggled it.
 */
export function ChainOfThought({ steps, running, children }: ChainOfThoughtProps) {
  const { t } = useTranslation();
  const streaming = useMessageStreaming();
  const failed = steps.filter(
    (part) => isToolUIPart(part) && toolLineState(part, streaming).status === "error",
  ).length;
  const status = running ? "running" : failed > 0 ? "error" : undefined;

  return (
    <Trace>
      <TraceStepCollapsible
        icon={status ? STATUS_ICONS[status] : <CircleCheckIcon />}
        title={
          running
            ? t(($) => $.common.states.working)
            : t(($) => $.chat.steps.took, { count: steps.length })
        }
        status={status}
        description={status === "error" && t(($) => $.chat.steps.failed, { count: failed })}
        autoOpen={running}
        keepMounted
      >
        <Trace>{children}</Trace>
      </TraceStepCollapsible>
    </Trace>
  );
}
