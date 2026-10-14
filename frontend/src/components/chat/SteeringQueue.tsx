import { CircleIcon, MessageSquarePlusIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { TraceStep, TraceStepTrigger } from "@/components/chat/TraceStep";
import { Trace, TraceCollapsible, TraceCollapsibleContent } from "@/components/ui/trace";
import type { SteeringMessage } from "@/hooks/chat/use-steering-queue";

interface SteeringQueueProps {
  queue: SteeringMessage[];
}

/** The messages sent while the assistant works, waiting for its next step. */
export function SteeringQueue({ queue }: SteeringQueueProps) {
  const { t } = useTranslation();

  if (queue.length === 0) return null;

  return (
    <TraceCollapsible defaultOpen>
      <TraceStepTrigger
        icon={<MessageSquarePlusIcon />}
        title={t(($) => $.chat.steering.queued, { count: queue.length })}
      />
      <TraceCollapsibleContent>
        <Trace>
          {queue.map((msg) => (
            <TraceStep key={msg.id} icon={<CircleIcon />} title={msg.text} />
          ))}
        </Trace>
      </TraceCollapsibleContent>
    </TraceCollapsible>
  );
}
