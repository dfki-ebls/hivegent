import { nanoid } from "nanoid";
import { useCallback, useEffect, useRef, useState } from "react";

export interface SteeringMessage {
  id: string;
  text: string;
}

export function useSteeringQueue(isStreaming: boolean, onDrain: (text: string) => void) {
  const [queue, setQueue] = useState<SteeringMessage[]>([]);

  const queueRef = useRef(queue);
  queueRef.current = queue;
  const onDrainRef = useRef(onDrain);
  onDrainRef.current = onDrain;
  const prevStreamingRef = useRef(isStreaming);

  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    prevStreamingRef.current = isStreaming;
    if (!isStreaming && wasStreaming && queueRef.current.length > 0) {
      const text = queueRef.current.map((m) => m.text).join("\n\n");
      setQueue([]);
      onDrainRef.current(text);
    }
  }, [isStreaming]);

  const enqueue = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setQueue((prev) => [...prev, { id: nanoid(), text: trimmed }]);
  }, []);

  return { queue, enqueue };
}
