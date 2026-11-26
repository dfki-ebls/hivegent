import type { QueryClient } from "@tanstack/react-query";
import { create } from "zustand";

import {
  subscribeJobs,
  TERMINAL_JOB_STATUSES,
  type ConversationActivity,
  type JobView,
  type LibraryKind,
  type WorkspaceChanged,
} from "@/api/jobs";

// Reconnect backoff for the SSE feed: start fast, ramp toward the cap only while
// reconnects keep failing (a sustained outage); a connection that stays open at
// least RECONNECT_HEALTHY_MS counts as healthy and resets the backoff to base.
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;
const RECONNECT_HEALTHY_MS = 5_000;
// How long a cleanly finished job lingers in the tray before it self-clears.
const AUTO_DISMISS_MS = 4_000;
// awaitJobSettled safety net: resolve anyway if the feed never delivers a
// terminal snapshot (a sustained outage); a healthy feed resolves far sooner.
const SETTLE_TIMEOUT_MS = 120_000;
// Remembered id cap (oldest-first eviction) bounding each dedup set.
const REMEMBERED_IDS_CAP = 500;

/** Add *id* to *ids*, evicting the oldest beyond the cap. */
function remember(ids: Set<string>, id: string): void {
  ids.add(id);

  if (ids.size > REMEMBERED_IDS_CAP) {
    const oldest = ids.values().next().value;

    if (oldest !== undefined) ids.delete(oldest);
  }
}

/** A set of listeners, with its `on…` registrar that returns the unsubscribe, and its emitter. */
function channel<A extends unknown[]>() {
  const listeners = new Set<(...args: A) => void>();

  return {
    on: (listener: (...args: A) => void): (() => void) => {
      listeners.add(listener);

      return () => {
        listeners.delete(listener);
      };
    },
    emit: (...args: A): void => listeners.forEach((listener) => listener(...args)),
  };
}

/** Combine several unsubscribes into one. */
const unsubscribeAll =
  (...unsubscribes: (() => void)[]) =>
  (): void =>
    unsubscribes.forEach((unsubscribe) => unsubscribe());

const jobSettled = channel<[JobView]>();
const jobStarted = channel<[JobView]>();
const workspaceChanged = channel<[WorkspaceChanged]>();
const conversationsChanged = channel<[]>();
const conversationActivity = channel<[ConversationActivity]>();
const libraryChanged = channel<[LibraryKind]>();
const sourcesChanged = channel<[]>();
/** Fired after the feed has replayed state on each connect. */
const feedReady = channel<[]>();

/**
 * Register a callback fired once per change to a workspace the user may read,
 * whoever made it: this tab, from its answer or the feed, whichever comes first,
 * another one, another group member, a job, the agent, or an MCP client.
 * Scopes a client holds refresh and paths it keeps follow here.
 * Returns an unsubscribe function.
 */
export const onWorkspaceChanged = workspaceChanged.on;

// Ids of the changes already published, since this tab applies its own change from the
// answer to `POST /changes` and the feed announces it too, in either order.
const publishedChangeIds = new Set<string>();

/**
 * Tell every {@link onWorkspaceChanged} listener how the workspaces changed, once per change,
 * since applying a change twice would carry paths on through a chain or swap.
 */
export function publishWorkspaceChanged(changed: WorkspaceChanged): void {
  if (publishedChangeIds.has(changed.id)) return;

  remember(publishedChangeIds, changed.id);
  workspaceChanged.emit(changed);
}

/**
 * Register a callback fired once when a job first appears in an active
 * (non-terminal) state, i.e. its work just began. Mirrors `jobSettled`
 * so a feature can react to a job's arrival (e.g. revealing the task tray).
 * Returns an unsubscribe function.
 */
export const onJobStarted = jobStarted.on;

/**
 * Register a callback fired whenever the workspaces may have changed, with the
 * change, or `"all"` after a feed handshake that may have missed some.
 * Returns an unsubscribe function.
 */
export function onWorkspaceStale(listener: (changed: WorkspaceChanged | "all") => void) {
  return unsubscribeAll(
    workspaceChanged.on(listener),
    feedReady.on(() => listener("all")),
  );
}

/**
 * Register a callback fired whenever the user's conversations may have
 * changed: a change by any tab, run, or import, or a feed handshake that may
 * have missed some. Returns an unsubscribe function.
 */
export function onConversationsStale(listener: () => void) {
  return unsubscribeAll(conversationsChanged.on(listener), feedReady.on(listener));
}

/**
 * Register a callback fired whenever the conversation *conversationId* shows
 * activity of *kind*: a turn starting, whoever started it, or its delegated
 * tasks changing, which a feed handshake that may have missed some counts as.
 * Returns an unsubscribe function.
 */
export function onConversationActivity(
  conversationId: string,
  kind: ConversationActivity["kind"],
  listener: () => void,
) {
  return unsubscribeAll(
    conversationActivity.on((activity) => {
      if (activity.conversation_id === conversationId && activity.kind === kind) listener();
    }),
    feedReady.on(listener),
  );
}

/**
 * Register a callback fired whenever the sync state of a source may have
 * changed (admins only), or after a feed handshake that may have missed some.
 * Returns an unsubscribe function.
 */
export function onSourcesStale(listener: () => void) {
  return unsubscribeAll(sourcesChanged.on(listener), feedReady.on(listener));
}

/**
 * Refetch the library of *kind* in *client* whenever it may have changed:
 * an install, edit, or removal by anyone in its scope, or a feed handshake
 * that may have missed some, then call *onRefetched*. Unobserved queries
 * refetch too, so the callback reads the fresh library. Returns the unsubscribe.
 */
export function syncLibraryQueries(
  client: QueryClient,
  kind: LibraryKind,
  onRefetched?: () => void,
): () => void {
  const refetch = () =>
    void client.invalidateQueries({ queryKey: [kind], refetchType: "all" }).then(onRefetched);

  return unsubscribeAll(
    libraryChanged.on((changed) => {
      if (changed === kind) refetch();
    }),
    feedReady.on(refetch),
  );
}

/**
 * Resolve once the job with `id` reaches a terminal state. Lets a caller that
 * submitted a job await its real completion, while the work itself still runs
 * off the request. Resolves
 * with `undefined` if the job already settled and was dismissed, or if the
 * feed never delivers the terminal snapshot before {@link SETTLE_TIMEOUT_MS}.
 */
export function awaitJobSettled(id: string): Promise<JobView | undefined> {
  // A fast job can settle before this subscribes, after which no further event
  // is guaranteed; resolve straight from the current snapshot so the caller
  // never waits on a transition that already fired.
  const current = useJobsStore.getState().jobs[id];
  if (current && isTerminal(current)) return Promise.resolve(current);

  // Already settled and dismissed before this subscribed: the terminal
  // transition fired once and is suppressed on re-seed, so a listener would
  // never fire, resolve now instead of hanging forever.
  if (settledJobIds.has(id)) return Promise.resolve(undefined);

  let off = () => {};
  let timer: ReturnType<typeof setTimeout> | undefined;
  // The listener and timeout only resolve; cleanup runs in `finally` (outside
  // the executor) so neither path calls back into the promise.
  return new Promise<JobView | undefined>((resolve) => {
    timer = setTimeout(() => resolve(useJobsStore.getState().jobs[id]), SETTLE_TIMEOUT_MS);
    off = jobSettled.on((job) => {
      if (job.id === id) resolve(job);
    });
  }).finally(() => {
    off();
    clearTimeout(timer);
  });
}

const isTerminal = (job: JobView) => TERMINAL_JOB_STATUSES.has(job.status);

// IDs whose terminal transition the store has already handled (settle handlers
// fired). The feed re-seeds retained terminal jobs on every reconnect, so this
// makes terminal handling idempotent: a re-sent snapshot updates state without
// re-firing handlers or resurrecting a job the user already dismissed.
const settledJobIds = new Set<string>();
const markSettled = (id: string) => remember(settledJobIds, id);

interface JobsStore {
  jobs: Record<string, JobView>;
  started: boolean;
  /** Open the job feed once; idempotent and self-reconnecting. */
  start: () => void;
  /**
   * Upsert a snapshot (from the feed or an immediate submit response). During
   * the feed's initial replay (`seeding`), the snapshot reflects current state
   * rather than a transition, so started/settled handlers are not fired for
   * jobs that were already running or had already finished when we connected.
   */
  upsert: (job: JobView, opts?: { seeding?: boolean }) => void;
  /** Drop a job from the tray (client-only). */
  dismiss: (id: string) => void;
}

export const useJobsStore = create<JobsStore>((set, get) => ({
  jobs: {},
  started: false,

  start: () => {
    if (get().started) return;
    set({ started: true });

    // One feed for the whole app session, re-opened whenever it drops.
    // `subscribeJobs` only settles when the connection ends, so each attempt
    // schedules the next: the backoff ramps while reconnects fail fast, but a
    // connection that stayed open resets it so a server restart reconnects
    // promptly instead of waiting out the cap.
    const attempt = async (delay: number): Promise<void> => {
      const openedAt = Date.now();
      // Each connect replays current state before live changes; treat that
      // seed as non-transitional until the ready marker arrives, so reloading
      // a page with finished jobs neither re-toasts nor flashes them, and
      // already-running jobs reappear in the tray without a "started" cue.
      let seeding = true;
      try {
        await subscribeJobs({
          onJob: (job) => get().upsert(job, { seeding }),
          onReady: () => {
            seeding = false;
            feedReady.emit();
          },
          onWorkspaceChanged: publishWorkspaceChanged,
          onConversationsChanged: conversationsChanged.emit,
          onConversationActivity: conversationActivity.emit,
          onLibraryChanged: libraryChanged.emit,
          onSourcesChanged: sourcesChanged.emit,
        });
      } catch {
        // A failed connect counts as a drop; the backoff below handles it.
      }
      const healthy = Date.now() - openedAt >= RECONNECT_HEALTHY_MS;
      // The first attempt starts at 0 for an instant connect; a failure must
      // still ramp, so floor the next delay at the base (otherwise 0 * 2 stays 0
      // and spins without backoff). Doubles to the cap from there: base, 2x,
      // 4x, ... while reconnects keep failing.
      const next = healthy
        ? RECONNECT_BASE_MS
        : Math.min(Math.max(delay * 2, RECONNECT_BASE_MS), RECONNECT_MAX_MS);
      reconnect(next);
    };

    const reconnect = (delay: number): void => {
      setTimeout(() => void attempt(delay), delay);
    };

    reconnect(0);
  },

  // Snapshots arrive from the submit response and the feed, which also re-seeds
  // every retained job on each (re)connect, so upsert is idempotent and
  // order-independent: drop a stale snapshot, never resurrect a dismissed job,
  // and fire start/settle listeners only on the first transition into the
  // matching state. A clean finish then lingers briefly; a failure stays until
  // dismissed.
  upsert: (job, opts) => {
    const seeding = opts?.seeding ?? false;
    const prev = get().jobs[job.id];

    // A fast job's terminal snapshot (from the feed) can beat its queued
    // snapshot (from the submit response); never let the older one overwrite it.
    if (prev && job.updated_at < prev.updated_at) return;

    const terminal = isTerminal(job);

    // Already handled this job's terminal transition: a re-seed or a stale late
    // snapshot must neither re-fire handlers nor resurrect a dismissed job.
    if (settledJobIds.has(job.id)) return;

    if (terminal) {
      // A terminal job we were not already tracking is not a transition we
      // witnessed: on the initial replay it is history (a job that finished
      // before we connected), so record it as handled but keep it off the tray
      // No stale completion flash, no toast, no refresh. A terminal snapshot
      // for a job we held as active (prev) is a real transition (e.g. one we
      // missed while briefly disconnected), so it falls through and settles.
      if (seeding && !prev) {
        markSettled(job.id);
        return;
      }

      set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));
      markSettled(job.id);
      jobSettled.emit(job);
      if (job.status !== "failed") {
        setTimeout(() => get().dismiss(job.id), AUTO_DISMISS_MS);
      }

      return;
    }

    set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));

    // First snapshot of a job that is starting now, not one already running
    // when we connected (a seed) and not a re-seed of one we already track:
    // announce that work began.
    if (!prev && !seeding) {
      jobStarted.emit(job);
    }
  },

  dismiss: (id) => {
    set((s) => {
      if (!(id in s.jobs)) return s;
      const next = { ...s.jobs };
      delete next[id];
      return { jobs: next };
    });
  },
}));
