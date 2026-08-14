import type { UIMessage } from "@ai-sdk/react";
import { useCallback, useEffect, useState } from "react";
import { Reasoning, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { ReasoningMarkdown } from "@/components/chat/markdown/ReasoningMarkdown";

type ReasoningUIPart = Extract<UIMessage["parts"][number], { type: "reasoning" }>;

/** Mirrors `AUTO_CLOSE_DELAY` in `ai-elements/reasoning.tsx`, which is not exported. */
const AUTO_COLLAPSE_MS = 1000;

interface ReasoningPartProps {
  part: ReasoningUIPart;
  duration?: number;
}

export function ReasoningPart({ part, duration }: ReasoningPartProps) {
  const isStreaming = part.state === "streaming";

  // `Reasoning` forces itself back open on any render where it streams and is
  // closed, so the block cannot be collapsed mid-thought. Owning `open` here and
  // passing no `onOpenChange` leaves its auto-open and auto-close inert.
  const [open, setOpen] = useState(isStreaming);
  const toggle = useCallback(() => setOpen((previous) => !previous), []);

  // Collapse once the thought finishes. Keyed on `isStreaming` alone, so
  // reopening the block later never re-arms it.
  useEffect(() => {
    if (isStreaming) return;

    const timer = setTimeout(() => setOpen(false), AUTO_COLLAPSE_MS);

    return () => clearTimeout(timer);
  }, [isStreaming]);

  if (!part.text && !isStreaming) return null;

  return (
    <Reasoning className="mb-0" isStreaming={isStreaming} duration={duration} open={open}>
      <ReasoningTrigger onClick={toggle} />
      <ReasoningMarkdown>{part.text}</ReasoningMarkdown>
    </Reasoning>
  );
}
