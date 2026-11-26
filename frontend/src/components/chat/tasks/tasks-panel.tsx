import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BotIcon, CheckIcon, CircleAlertIcon, CircleSlashIcon, SquareIcon } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { type DelegatedTask, syncTaskQueries, taskMutations, taskQueries } from "@/api/tasks";
import { keyPrefix } from "@/i18n";
import { MessageLiveProvider, useConversationId } from "@/components/chat/chat-context";
import { ChainOfThought } from "@/components/chat/chain-of-thought";
import { MarkdownText } from "@/components/chat/markdown/markdown-text";
import { MessagePart } from "@/components/chat/message-part";
import { TraceStepCollapsible } from "@/components/chat/trace-step";
import { StatusPanel } from "@/components/common/status-panel";
import { TooltipButton } from "@/components/common/tooltip-button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Trace } from "@/components/ui/trace";
import { blocksOf } from "@/lib/chat/layout";
import type { ChatMessage } from "@/lib/chat/message";
import { indexToolData, type Live } from "@/lib/chat/tool-part";

const T_OPTIONS = keyPrefix(($) => $.documents.tasks);

/**
 * The tasks the conversation delegated to subagents, newest first, each a line that opens
 * to everything its subagent did. They run in the background while the chat goes on,
 * and the feed keeps them current.
 */
export function TasksPanel() {
  const conversationId = useConversationId();

  return conversationId === null ? (
    <NoTasks />
  ) : (
    <ConversationTasks conversationId={conversationId} />
  );
}

function NoTasks({ loading = false }: { loading?: boolean }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <StatusPanel
      icon={BotIcon}
      loading={loading}
      title={t(($) => $.emptyTitle)}
      description={t(($) => $.emptyDescription)}
      className="h-full"
    />
  );
}

function ConversationTasks({ conversationId }: { conversationId: string }) {
  const client = useQueryClient();
  const { data: tasks, isPending } = useQuery(taskQueries.list(conversationId));

  useEffect(() => syncTaskQueries(client, conversationId), [client, conversationId]);

  if (isPending || !tasks?.length) return <NoTasks loading={isPending} />;

  return (
    <ScrollArea className="h-full">
      <Trace className="p-4">
        {tasks.toReversed().map((task) => (
          <TaskLine key={task.id} conversationId={conversationId} task={task} />
        ))}
      </Trace>
    </ScrollArea>
  );
}

function TaskIcon({ task }: { task: DelegatedTask }) {
  if (task.status === "running") return <BotIcon />;
  if (task.outcome === "ok") return <CheckIcon />;
  if (task.outcome === "cancelled") return <CircleSlashIcon />;

  return <CircleAlertIcon />;
}

/** A task as a line that stays closed until the user opens it to its subagent's run. */
function TaskLine({ conversationId, task }: { conversationId: string; task: DelegatedTask }) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const running = task.status === "running";
  const stop = useMutation(taskMutations.stop());
  const state = running ? "running" : (task.outcome ?? "error");

  return (
    <TraceStepCollapsible
      icon={<TaskIcon task={task} />}
      title={
        <span className="flex min-w-0 items-center gap-2">
          <Badge variant="secondary" className="font-mono">
            {task.agent_name}
          </Badge>
          <span className="truncate">{task.prompt}</span>
        </span>
      }
      status={running ? "running" : undefined}
      description={t(($) => $.status[state])}
      contentClassName="flex flex-col gap-3"
    >
      <div className="flex items-start gap-2">
        <p className="min-w-0 flex-1 text-sm whitespace-pre-wrap text-muted-foreground">
          {task.prompt}
        </p>
        {running && (
          <TooltipButton
            label={t(($) => $.stop)}
            disabled={stop.isPending}
            onClick={() => stop.mutate({ conversationId, taskId: task.id })}
          >
            <SquareIcon />
          </TooltipButton>
        )}
      </div>
      <TaskTranscript conversationId={conversationId} task={task} />
    </TraceStepCollapsible>
  );
}

/** What the task's subagent did so far, its reasoning, calls, and report, rendered like a reply. */
function TaskTranscript({ conversationId, task }: { conversationId: string; task: DelegatedTask }) {
  const { data: messages } = useQuery(taskQueries.messages(conversationId, task.id));
  const live: Live = task.status === "running" ? "streaming" : "stale";

  if (!messages) return task.output && <MarkdownText>{task.output}</MarkdownText>;

  return (
    <MessageLiveProvider value={live}>
      {messages
        .filter((message) => message.role === "assistant")
        .map((message) => (
          <TaskReply key={message.id} message={message} live={live} />
        ))}
    </MessageLiveProvider>
  );
}

function TaskReply({ message, live }: { message: ChatMessage; live: Live }) {
  const toolData = indexToolData(message.parts);

  return blocksOf(message.parts, live).map((block) =>
    block.kind === "steps" ? (
      <ChainOfThought key={block.index} steps={block.parts} live={live} running={block.running}>
        {block.parts.map((part, key) => (
          <MessagePart key={key} part={part} toolData={toolData} prompt={false} />
        ))}
      </ChainOfThought>
    ) : (
      <MessagePart key={block.index} part={block.part} toolData={toolData} prompt={false} />
    ),
  );
}
