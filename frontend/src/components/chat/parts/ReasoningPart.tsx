import type { UIMessage } from "@ai-sdk/react";
import { BrainIcon } from "lucide-react";
import { type ComponentProps, useState } from "react";
import { useTranslation } from "react-i18next";
import { ReasoningMarkdown } from "@/components/chat/markdown/MarkdownText";
import { TraceStep, TraceStepTrigger } from "@/components/chat/TraceStep";
import { TraceCollapsible, TraceCollapsibleContent } from "@/components/ui/trace";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.reasoning);

type ReasoningUIPart = Extract<UIMessage["parts"][number], { type: "reasoning" }>;

interface ReasoningPartProps {
  part: ReasoningUIPart;
  /** Seconds the backend measured, known once the turn's metadata arrives. */
  duration?: number;
}

/**
 * One reasoning part as a trace line.
 * It opens while the model thinks and closes once it is done, unless the user toggled it.
 */
export function ReasoningPart({ part, duration }: ReasoningPartProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const [open, setOpen] = useState<boolean>();
  const running = part.state === "streaming";

  const step: ComponentProps<typeof TraceStep> = {
    icon: <BrainIcon />,
    status: running ? "running" : undefined,
    title: running
      ? t(($) => $.thinking)
      : duration === undefined
        ? t(($) => $.thought)
        : t(($) => $.thoughtFor, { count: duration }),
  };

  // Nothing to open before the model has written anything.
  if (!part.text) return running ? <TraceStep {...step} /> : null;

  return (
    <TraceCollapsible open={open ?? running} onOpenChange={setOpen}>
      <TraceStepTrigger {...step} />
      <TraceCollapsibleContent>
        <ReasoningMarkdown>{part.text}</ReasoningMarkdown>
      </TraceCollapsibleContent>
    </TraceCollapsible>
  );
}
