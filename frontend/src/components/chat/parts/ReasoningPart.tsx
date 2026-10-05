import type { UIMessage } from "@ai-sdk/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Reasoning, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { ReasoningMarkdown } from "@/components/chat/markdown/ReasoningMarkdown";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.reasoning);

type ReasoningUIPart = Extract<UIMessage["parts"][number], { type: "reasoning" }>;

interface ReasoningPartProps {
  part: ReasoningUIPart;
  duration?: number;
}

export function ReasoningPart({ part, duration }: ReasoningPartProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const isStreaming = part.state === "streaming";
  // The backend's duration only reaches the client with the turn's metadata, at
  // the end of the whole run. Handing it to `Reasoning` then would switch its
  // duration prop from uncontrolled to controlled mid-life, so a part that
  // mounted while it was still streaming keeps the duration the component timed
  // itself (the same measurement, a fraction of a second apart) and only a part
  // that mounts with its metadata, on reload, shows the persisted one.
  const [mountDuration] = useState(duration);

  if (!part.text && !isStreaming) return null;

  return (
    <Reasoning className="mb-0" isStreaming={isStreaming} duration={mountDuration}>
      <ReasoningTrigger
        getThinkingMessage={(streaming, seconds) => {
          if (streaming || seconds === 0) {
            return <Shimmer duration={1}>{t(($) => $.thinking)}</Shimmer>;
          }

          return (
            <p>
              {seconds === undefined
                ? t(($) => $.thoughtBriefly)
                : t(($) => $.thoughtFor, { count: seconds })}
            </p>
          );
        }}
      />
      <ReasoningMarkdown>{part.text}</ReasoningMarkdown>
    </Reasoning>
  );
}
