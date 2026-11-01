import type { ReasoningUIPart } from "ai";
import { BrainIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ReasoningMarkdown } from "@/components/chat/markdown/markdown-text";
import { TraceStepCollapsible } from "@/components/chat/trace-step";
import { useMessageLive } from "@/components/chat/chat-context";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.reasoning);

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
  // Only the message the chat streams into can still be written.
  const running = useMessageLive() === "streaming" && part.state === "streaming";

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
