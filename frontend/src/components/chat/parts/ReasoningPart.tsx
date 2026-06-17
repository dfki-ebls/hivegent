import type { UIMessage } from "@ai-sdk/react";
import { Reasoning, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { ReasoningMarkdown } from "@/components/chat/markdown/ReasoningMarkdown";

type ReasoningUIPart = Extract<UIMessage["parts"][number], { type: "reasoning" }>;

interface ReasoningPartProps {
  part: ReasoningUIPart;
}

export function ReasoningPart({ part }: ReasoningPartProps) {
  if (!part.text && part.state !== "streaming") return null;

  return (
    <Reasoning isStreaming={part.state === "streaming"}>
      <ReasoningTrigger />
      <ReasoningMarkdown>{part.text}</ReasoningMarkdown>
    </Reasoning>
  );
}
