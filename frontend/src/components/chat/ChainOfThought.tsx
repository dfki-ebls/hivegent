import { isToolUIPart } from "ai";
import { CircleCheckIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ChatPart } from "@/components/chat/MessagePart";
import { TraceStep, TraceStepCollapsible } from "@/components/chat/TraceStep";
import { Spinner } from "@/components/ui/spinner";
import { Trace } from "@/components/ui/trace";
import { toolLineState } from "@/lib/chat/tool-part";
import { useMessageStreaming } from "@/hooks/chat/use-message-streaming";

/** The line that says the model works while nothing else on screen does, a step of the trace it sits in. */
export function ProcessingStep() {
  const { t } = useTranslation();

  return (
    <TraceStep icon={<Spinner />} title={t(($) => $.chat.steps.processing)} status="running" />
  );
}

/** The processing line on its own, where the next chain of thought would start. */
export function ProcessingTrace() {
  return (
    <Trace>
      <ProcessingStep />
    </Trace>
  );
}

interface ChainOfThoughtProps {
  steps: ChatPart[];
  /** Whether the agent still works on these steps, which nothing follows yet. */
  running: boolean;
  /** The rendered steps. */
  children: ReactNode;
}

/**
 * A run of steps as one summary line that opens to them.
 * Failed steps are routine for an agent, so the line counts them without alarming.
 * It opens while the agent works and closes once the reply moves on, unless the user toggled it.
 */
export function ChainOfThought({ steps, running, children }: ChainOfThoughtProps) {
  const { t } = useTranslation();
  const streaming = useMessageStreaming();
  const failed = steps.filter(
    (part) => isToolUIPart(part) && toolLineState(part, streaming).failed,
  ).length;

  return (
    <Trace>
      <TraceStepCollapsible
        icon={running ? <Spinner /> : <CircleCheckIcon />}
        title={
          running
            ? t(($) => $.common.states.working)
            : t(($) => $.chat.steps.took, { count: steps.length })
        }
        status={running ? "running" : undefined}
        description={!running && failed > 0 && t(($) => $.chat.steps.failed, { count: failed })}
        autoOpen={running}
        keepMounted
      >
        <Trace>{children}</Trace>
      </TraceStepCollapsible>
    </Trace>
  );
}
