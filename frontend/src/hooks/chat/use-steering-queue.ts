import { nanoid } from "nanoid";
import { useCallback, useEffect, useEffectEvent, useRef, useState } from "react";

export interface SteeringMessage {
  id: string;
  text: string;
}

export function useSteeringQueue(isStreaming: boolean, onDrain: (text: string) => void) {
  const [queue, setQueue] = useState<SteeringMessage[]>([]);
  const prevStreamingRef = useRef(isStreaming);

  const drain = useEffectEvent(() => {
    if (queue.length === 0) return;
    setQueue([]);
    onDrain(queue.map((m) => m.text).join("\n\n"));
  });

  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    prevStreamingRef.current = isStreaming;
    if (wasStreaming && !isStreaming) drain();
  }, [isStreaming]);

  const enqueue = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setQueue((prev) => [...prev, { id: nanoid(), text: trimmed }]);
  }, []);

  return { queue, enqueue };
}
