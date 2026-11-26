import { type QueryClient, mutationOptions, queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { requestJson, requestVoid } from "@/api/client";
import { conversationUrl, FEED_REFETCH_DELAY_MS, parseMessages } from "@/api/conversations";
import type { ChatMessage } from "@/lib/chat/message";
import { onConversationActivity } from "@/stores/jobs-store";

/** A task the main agent of a conversation delegated to a subagent (see backend `DelegatedTask`). */
export const DelegatedTaskSchema = z.object({
  id: z.string(),
  parent_id: z.string().nullable(),
  agent_name: z.string(),
  prompt: z.string(),
  status: z.enum(["running", "finished"]),
  outcome: z
    .enum(["ok", "timeout", "budget", "failed", "contained", "cancelled", "error"])
    .nullable(),
  output: z.string(),
  started_at: z.number(),
  finished_at: z.number().nullable(),
  user_stopped: z.boolean(),
});
export type DelegatedTask = z.infer<typeof DelegatedTaskSchema>;

function tasksUrl(conversationId: string, taskId?: string): string {
  return conversationUrl(
    conversationId,
    taskId === undefined ? "/tasks" : `/tasks/${encodeURIComponent(taskId)}`,
  );
}

function listTasks(conversationId: string): Promise<DelegatedTask[]> {
  return requestJson(tasksUrl(conversationId), "listTasks", z.array(DelegatedTaskSchema));
}

async function getTaskMessages(conversationId: string, taskId: string): Promise<ChatMessage[]> {
  return parseMessages(
    await requestJson(
      `${tasksUrl(conversationId, taskId)}/messages`,
      "fetchTaskMessages",
      z.array(z.unknown()),
    ),
  );
}

function stopTask({ conversationId, taskId }: { conversationId: string; taskId: string }) {
  return requestVoid(tasksUrl(conversationId, taskId), "stopTask", { method: "DELETE" });
}

const messagesKey = (conversationId: string) =>
  ["conversations", "task-messages", conversationId] as const;

export const taskQueries = {
  /** The tasks of a conversation, oldest first. */
  list: (conversationId: string) =>
    queryOptions({
      queryKey: ["conversations", "tasks", conversationId],
      queryFn: () => listTasks(conversationId),
    }),
  /** The messages of a task's subagent so far. */
  messages: (conversationId: string, taskId: string) =>
    queryOptions({
      queryKey: [...messagesKey(conversationId), taskId],
      queryFn: () => getTaskMessages(conversationId, taskId),
    }),
};

/**
 * Refetch the tasks of *conversationId* in *client* whenever the feed says they may have changed,
 * once for a burst of changes, with the transcripts on screen, of which a resumed task may be any.
 * Returns the unsubscribe.
 */
export function syncTaskQueries(client: QueryClient, conversationId: string): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const unsubscribe = onConversationActivity(conversationId, "tasks", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      void client.invalidateQueries({ queryKey: taskQueries.list(conversationId).queryKey });
      void client.invalidateQueries({ queryKey: messagesKey(conversationId) });
    }, FEED_REFETCH_DELAY_MS);
  });

  return () => {
    clearTimeout(timer);
    unsubscribe();
  };
}

export const taskMutations = {
  /** Stop a running task, the feed reflects the resulting state. */
  stop: () =>
    mutationOptions({ mutationKey: ["conversations", "tasks", "stop"], mutationFn: stopTask }),
};
