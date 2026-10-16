import type { UIMessage } from "@ai-sdk/react";
import { BrainIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ReasoningMarkdown } from "@/components/chat/markdown/MarkdownText";
import { TraceStepCollapsible } from "@/components/chat/TraceStep";
import { useMessageStreaming } from "@/hooks/chat/use-message-streaming";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.reasoning);

type ReasoningUIPart = Extract<UIMessage["parts"][number], { type: "reasoning" }>;

/** Whether the model is still writing *part*, which only the message the chat streams into can be. */
export function isReasoningRunning(part: ReasoningUIPart, streaming: boolean): boolean {
  return streaming && part.state === "streaming";
}

interface ReasoningPartProps {
  part: ReasoningUIPart;
  /** Seconds the backend measured, known once the turn's metadata arrives. */
  duration?: number;
}

/**
 * One reasoning part as a trace line, which opens to the thinking once there is any.
 * It opens while the model thinks and closes once it is done, unless the user toggled it.
 */
export function ReasoningPart({ part, duration }: ReasoningPartProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const running = isReasoningRunning(part, useMessageStreaming());

  return (
    <TraceStepCollapsible
      icon={<BrainIcon />}
      status={running ? "running" : undefined}
      title={running ? t(($) => $.thinking) : t(($) => $.thought)}
      description={
        running || duration === undefined ? undefined : t(($) => $.seconds, { count: duration })
      }
      autoOpen={running}
    >
      {part.text && <ReasoningMarkdown>{part.text}</ReasoningMarkdown>}
    </TraceStepCollapsible>
  );
}
