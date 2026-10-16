import { CircleIcon, ListTodoIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { TraceStep, TraceStepCollapsible } from "@/components/chat/TraceStep";
import { Trace } from "@/components/ui/trace";
import type { SteeringMessage } from "@/hooks/chat/use-steering-queue";

/** The messages sent while the assistant works, waiting for its next step. */
export function SteeringQueue({ queue }: { queue: SteeringMessage[] }) {
  const { t } = useTranslation();

  if (queue.length === 0) return null;

  return (
    <Trace>
      <TraceStepCollapsible
        icon={<ListTodoIcon />}
        title={t(($) => $.chat.steering.queued, { count: queue.length })}
        autoOpen
      >
        <Trace>
          {queue.map((msg) => (
            <TraceStep key={msg.id} icon={<CircleIcon />} title={msg.text} />
          ))}
        </Trace>
      </TraceStepCollapsible>
    </Trace>
  );
}
