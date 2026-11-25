import { CircleIcon, ListTodoIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { TraceStep, TraceStepCollapsible } from "@/components/chat/trace-step";
import { Trace } from "@/components/ui/trace";
import { ConfirmDialog } from "@/components/confirm-dialog";
import type { SteeringLeave, SteeringMessage } from "@/hooks/chat/use-steering-queue";

interface SteeringQueueProps {
  queue: SteeringMessage[];
  leaving: SteeringLeave;
}

/** The messages sent while the assistant works, waiting for its next step, and the question before leaving them. */
export function SteeringQueue({ queue, leaving }: SteeringQueueProps) {
  const { t } = useTranslation();

  return (
    <>
      <ConfirmDialog
        open={leaving.status === "blocked"}
        onOpenChange={(open) => {
          if (!open) leaving.reset?.();
        }}
        title={t(($) => $.chat.steering.leaveTitle)}
        description={t(($) => $.chat.steering.leaveDescription, { count: queue.length })}
        confirmLabel={t(($) => $.chat.steering.leave)}
        onConfirm={() => leaving.proceed?.()}
      />
      {queue.length > 0 && (
        <Trace>
          <TraceStepCollapsible
            icon={<ListTodoIcon />}
            title={t(($) => $.chat.steering.queued, { count: queue.length })}
            autoOpen
          >
            <Trace>
              {queue.map((msg) => (
                <TraceStep
                  key={msg.id}
                  icon={<CircleIcon />}
                  title={
                    msg.text ||
                    msg.files
                      .map((file) => file.filename ?? t(($) => $.chat.messages.attachedImage))
                      .join(", ")
                  }
                />
              ))}
            </Trace>
          </TraceStepCollapsible>
        </Trace>
      )}
    </>
  );
}
