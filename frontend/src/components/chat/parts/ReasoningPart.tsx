import type { UIMessage } from "@ai-sdk/react";
import { Reasoning, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { ReasoningMarkdown } from "@/components/chat/markdown/ReasoningMarkdown";

type ReasoningUIPart = Extract<UIMessage["parts"][number], { type: "reasoning" }>;

interface ReasoningPartProps {
  part: ReasoningUIPart;
}

export function ReasoningPart({ part }: ReasoningPartProps) {
  // pydantic-ai stores raw chain-of-thought in providerMetadata when
  // the model (e.g. gpt-oss) doesn't produce reasoning summaries.
  const reasoningText =
    part.text ||
    (
      part.providerMetadata?.pydantic_ai as
        | { provider_details?: { raw_content?: string[] } }
        | undefined
    )?.provider_details?.raw_content?.join("\n\n") ||
    "";

  if (!reasoningText && part.state !== "streaming") return null;

  return (
    <Reasoning isStreaming={part.state === "streaming"}>
      <ReasoningTrigger />
      <ReasoningMarkdown>{reasoningText}</ReasoningMarkdown>
    </Reasoning>
  );
}
