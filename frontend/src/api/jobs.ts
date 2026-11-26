import { mutationOptions } from "@tanstack/react-query";
import { z } from "zod";

import { checkedResponse, requestVoid } from "@/api/client";
import { apiUrl } from "@/lib/health";

/** Lifecycle status of a background job. */
const JobStatusSchema = z.enum(["queued", "running", "succeeded", "failed", "cancelled"]);
type JobStatus = z.infer<typeof JobStatusSchema>;

/** Discrete progress of a job (e.g. files processed in a collection). */
const JobProgressSchema = z.object({
  current: z.number(),
  total: z.number(),
});

/**
 * Snapshot of a background job, the generic shape the `/jobs` feed emits and
 * the job tray renders, independent of which feature submitted it.
 */
export const JobViewSchema = z.object({
  id: z.string(),
  kind: z.string(),
  title: z.string(),
  status: JobStatusSchema,
  stage: z.string().nullable(),
  progress: JobProgressSchema.nullable(),
  error: z.string().nullable(),
  created_at: z.number(),
  updated_at: z.number(),
});
export type JobView = z.infer<typeof JobViewSchema>;

/** Marks the end of the job feed's initial replay (see backend `FeedReady`). */
const FeedReadySchema = z.object({ type: z.literal("ready") });

/**
 * A moved document file or directory, both paths canonical and without a
 * trailing slash, and whether it replaces a file the same change deletes.
 */
export const PathMoveSchema = z.object({
  source: z.string(),
  destination: z.string(),
  is_dir: z.boolean(),
  replaces: z.boolean(),
});

/**
 * The workspaces a change touched and the paths it moved and deleted (see
 * backend `WorkspaceChanged`), as the feed announces it to everyone who can
 * see them and `POST /changes` answers, both under the same server-assigned id.
 * Sources and deletes name the workspace before the change and destinations
 * the one after it, so they apply at once.
 */
export const WorkspaceChangedSchema = z.object({
  type: z.literal("workspace-changed"),
  id: z.string(),
  scopes: z.array(z.string()),
  moves: z.array(PathMoveSchema),
  deletes: z.array(z.string()),
});
export type WorkspaceChanged = z.infer<typeof WorkspaceChangedSchema>;

/** Marks a change to the user's conversations (see backend `ConversationsChanged`). */
const ConversationsChangedSchema = z.object({ type: z.literal("conversations-changed") });

/**
 * Marks a turn starting or the delegated tasks changing in one of the user's conversations
 * (see backend `ConversationActivity`).
 */
const ConversationActivitySchema = z.object({
  type: z.literal("conversation-activity"),
  conversation_id: z.string(),
  kind: z.enum(["turn", "tasks"]),
});
export type ConversationActivity = z.infer<typeof ConversationActivitySchema>;

/** The libraries the feed announces changes of, each named like the root of its query keys. */
export type LibraryKind = "skills";

/** Marks a change to a skill library the user sees (see backend `LibraryChanged`). */
const LibraryChangedSchema = z.object({
  type: z.literal("library-changed"),
  kind: z.enum(["skills"]),
});

/** Marks a change to the sync state of a source, which only admins' feeds carry (see backend `SourceChanged`). */
const SourceChangedSchema = z.object({ type: z.literal("source-changed") });

/** A job feed event: a job snapshot, the seed-complete marker, or a change. */
export const FeedEventSchema = z.union([
  JobViewSchema,
  FeedReadySchema,
  WorkspaceChangedSchema,
  ConversationsChangedSchema,
  ConversationActivitySchema,
  LibraryChangedSchema,
  SourceChangedSchema,
]);

/** What {@link subscribeJobs} calls for each kind of feed event. */
export interface FeedHandlers {
  onJob: (job: JobView) => void;
  onReady: () => void;
  onWorkspaceChanged: (changed: WorkspaceChanged) => void;
  onConversationsChanged: () => void;
  onConversationActivity: (activity: ConversationActivity) => void;
  onLibraryChanged: (kind: LibraryKind) => void;
  onSourcesChanged: () => void;
}

/** Terminal job statuses, after which no further updates arrive. */
export const TERMINAL_JOB_STATUSES: ReadonlySet<JobStatus> = new Set([
  "succeeded",
  "failed",
  "cancelled",
]);

/** Active job statuses, the complement of the terminal ones. */
export const ACTIVE_JOB_STATUSES: ReadonlySet<JobStatus> = new Set(["queued", "running"]);

/** Request cancellation of a background job. Idempotent server-side. */
function cancelJob(id: string): Promise<void> {
  return requestVoid(apiUrl(`/jobs/${encodeURIComponent(id)}`), "cancelJob", {
    method: "DELETE",
  });
}

/**
 * Read the `data:` lines of a server-sent event stream, validating each against
 * *schema*, until the connection ends. Malformed events are skipped.
 */
async function readSseEvents<T>(
  res: Response,
  schema: z.ZodType<T>,
  onEvent: (event: T) => void,
): Promise<void> {
  const reader = res.body?.getReader();

  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();

    if (done) return;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();

      if (!trimmed.startsWith("data: ")) continue;

      let raw: unknown;

      try {
        raw = JSON.parse(trimmed.slice(6));
      } catch {
        continue;
      }

      const event = schema.safeParse(raw);

      if (event.success) onEvent(event.data);
    }
  }
}

/**
 * Subscribe to the caller's job feed, invoking `onJob` for every snapshot,
 * `onReady` once the initial replay of current jobs is complete,
 * `onWorkspaceChanged` for every change to a workspace the user may read,
 * `onConversationsChanged` for every change to the user's conversations,
 * `onConversationActivity` for every turn starting or tasks changing in one of them, and
 * `onLibraryChanged` for every change to a library the user sees,
 * `onSourcesChanged` for every change to the sync state of a source (admins only),
 * whoever made them, until the connection ends or `signal` aborts. The feed
 * seeds the current jobs on connect, so a reconnect re-converges on live state
 * while the caller tells the seed apart from later transitions.
 */
export async function subscribeJobs(
  {
    onJob,
    onReady,
    onWorkspaceChanged,
    onConversationsChanged,
    onConversationActivity,
    onLibraryChanged,
    onSourcesChanged,
  }: FeedHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await checkedResponse(apiUrl("/jobs/events"), "jobFeed", { signal });

  // The feed never ends on its own, so its end is a dropped connection, which
  // the caller's reconnect loop handles.
  await readSseEvents(res, FeedEventSchema, (event) => {
    if (!("type" in event)) onJob(event);
    else if (event.type === "ready") onReady();
    else if (event.type === "conversations-changed") onConversationsChanged();
    else if (event.type === "conversation-activity") onConversationActivity(event);
    else if (event.type === "library-changed") onLibraryChanged(event.kind);
    else if (event.type === "source-changed") onSourcesChanged();
    else onWorkspaceChanged(event);
  });

  throw new Error("Stream ended without completion event");
}

export const jobMutations = {
  /** Request cancellation of a job, the job feed reflects the resulting state. */
  cancel: () => mutationOptions({ mutationKey: ["jobs", "cancel"], mutationFn: cancelJob }),
};
