import type { FileUIPart } from "ai";
import { nanoid } from "nanoid";
import { useCallback, useEffect, useEffectEvent, useRef, useState } from "react";

export interface SteeringMessage {
  id: string;
  text: string;
  files: FileUIPart[];
}

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

  const enqueue = useCallback((text: string, files: FileUIPart[]) => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;
    setQueue((prev) => [...prev, { id: nanoid(), text: trimmed, files }]);
  }, []);

  return { queue, enqueue };
}
