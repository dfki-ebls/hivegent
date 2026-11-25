import { useBlocker } from "@tanstack/react-router";
import type { FileUIPart } from "ai";
import { useEffect, useEffectEvent, useRef, useState } from "react";

/** Whether a navigation waits for the user to leave the queued messages behind. */
export type SteeringLeave = ReturnType<typeof useSteeringQueue>["leaving"];

export interface SteeringMessage {
  id: string;
  text: string;
  files: FileUIPart[];
}

/**
 * The messages sent while the assistant works, sent as one once it stops.
 * They live only in this tab, so leaving the conversation or the page asks
 * first while any wait, through `leaving` for a navigation in the app.
 */
export function useSteeringQueue(
  isStreaming: boolean,
  onDrain: (text: string, files: FileUIPart[]) => void,
) {
  const [queue, setQueue] = useState<SteeringMessage[]>([]);
  const prevStreamingRef = useRef(isStreaming);

  const drain = useEffectEvent(() => {
    if (queue.length === 0) return;
    setQueue([]);
    onDrain(
      queue
        .map((m) => m.text)
        .filter(Boolean)
        .join("\n\n"),
      queue.flatMap((m) => m.files),
    );
  });

  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    prevStreamingRef.current = isStreaming;
    if (wasStreaming && !isStreaming) drain();
  }, [isStreaming]);

  const enqueue = (text: string, files: FileUIPart[]) => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;
    setQueue((prev) => [...prev, { id: crypto.randomUUID(), text: trimmed, files }]);
  };

  const leaving = useBlocker({
    shouldBlockFn: ({ current, next }) => current.pathname !== next.pathname,
    disabled: queue.length === 0,
    enableBeforeUnload: queue.length > 0,
    withResolver: true,
  });

  return { queue, enqueue, leaving };
}
