import { MessageSquarePlusIcon } from "lucide-react";
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
  if (queue.length === 0) return null;
  return (
    <Queue>
      <QueueSection>
        <QueueSectionTrigger>
          <QueueSectionLabel
            count={queue.length}
            label={queue.length === 1 ? "queued message" : "queued messages"}
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
