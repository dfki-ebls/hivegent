import { MessageSquarePlusIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  Queue,
  QueueItem,
  QueueItemContent,
  QueueItemIndicator,
  QueueList,
  QueueSection,
  QueueSectionContent,
  QueueSectionLabel,
  QueueSectionTrigger,
} from "@/components/ai-elements/queue";
import type { SteeringMessage } from "@/hooks/chat/use-steering-queue";

interface SteeringQueueProps {
  queue: SteeringMessage[];
}

export function SteeringQueue({ queue }: SteeringQueueProps) {
  const { t } = useTranslation();

  if (queue.length === 0) return null;

  return (
    <Queue>
      <QueueSection>
        <QueueSectionTrigger>
          <QueueSectionLabel
            count={queue.length}
            label={t(($) => $.chat.steering.queued, { count: queue.length })}
            icon={<MessageSquarePlusIcon className="size-4" />}
          />
        </QueueSectionTrigger>
        <QueueSectionContent>
          <QueueList>
            {queue.map((msg) => (
              <QueueItem key={msg.id}>
                <div className="flex items-center gap-2">
                  <QueueItemIndicator />
                  <QueueItemContent>{msg.text}</QueueItemContent>
                </div>
              </QueueItem>
            ))}
          </QueueList>
        </QueueSectionContent>
      </QueueSection>
    </Queue>
  );
}
