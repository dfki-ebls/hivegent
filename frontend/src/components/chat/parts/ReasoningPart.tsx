import type { UIMessage } from "@ai-sdk/react";
import { Reasoning, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { ReasoningMarkdown } from "@/components/chat/markdown/ReasoningMarkdown";

type ReasoningUIPart = Extract<UIMessage["parts"][number], { type: "reasoning" }>;

interface ReasoningPartProps {
  part: ReasoningUIPart;
  duration?: number;
}

export function ReasoningPart({ part, duration }: ReasoningPartProps) {
  const isStreaming = part.state === "streaming";

  if (!part.text && !isStreaming) return null;

  return (
    <Reasoning className="mb-0" isStreaming={isStreaming} duration={duration}>
      <ReasoningTrigger />
      <ReasoningMarkdown>{part.text}</ReasoningMarkdown>
    </Reasoning>
  );
}
