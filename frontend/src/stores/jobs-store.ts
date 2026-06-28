import { create } from "zustand";

import { cancelJob, subscribeJobs } from "@/lib/api";
import { TERMINAL_JOB_STATUSES, type JobView } from "@/lib/types";

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
// Remembered terminal-id cap (oldest-first eviction) bounding the dedup set.
const SETTLED_IDS_CAP = 500;

type JobListener = (job: JobView) => void;

const settledListeners = new Set<JobListener>();
const startedListeners = new Set<JobListener>();

/**
 * Register a callback fired once when any job reaches a terminal state. Keeps
 * the job store generic: a feature (e.g. documents) reacts only to its own job
 * kinds. Returns an unsubscribe function.
 */
export function onJobSettled(listener: JobListener): () => void {
  settledListeners.add(listener);
  return () => {
    settledListeners.delete(listener);
  };
}

/**
 * Register a callback fired once when a job first appears in an active
 * (non-terminal) state — i.e. its work just began. Mirrors {@link onJobSettled}
 * so a feature can react to a job's arrival (e.g. revealing the task tray).
 * Returns an unsubscribe function.
 */
export function onJobStarted(listener: JobListener): () => void {
  startedListeners.add(listener);
  return () => {
    startedListeners.delete(listener);
  };
}

/**
 * Resolve once the job with `id` reaches a terminal state. Lets a caller that
 * submitted a job (e.g. the dialog's rechunk) await its real completion to
 * refresh inline, while the work itself still runs off the request. Resolves
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
  // never fire — resolve now instead of hanging forever.
  if (settledJobIds.has(id)) return Promise.resolve(undefined);

  let off = () => {};
  let timer: ReturnType<typeof setTimeout> | undefined;
  // The listener and timeout only resolve; cleanup runs in `finally` (outside
  // the executor) so neither path calls back into the promise.
  return new Promise<JobView | undefined>((resolve) => {
    timer = setTimeout(() => resolve(useJobsStore.getState().jobs[id]), SETTLE_TIMEOUT_MS);
    off = onJobSettled((job) => {
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

function markSettled(id: string): void {
  settledJobIds.add(id);
  if (settledJobIds.size > SETTLED_IDS_CAP) {
    const oldest = settledJobIds.values().next().value;
    if (oldest !== undefined) settledJobIds.delete(oldest);
  }
}

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
  /** Request cancellation; the feed reflects the resulting state. */
  cancel: (id: string) => Promise<void>;
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
        await subscribeJobs(
          (job) => get().upsert(job, { seeding }),
          () => {
            seeding = false;
          },
        );
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
      // — no stale completion flash, no toast, no refresh. A terminal snapshot
      // for a job we held as active (prev) is a real transition (e.g. one we
      // missed while briefly disconnected), so it falls through and settles.
      if (seeding && !prev) {
        markSettled(job.id);
        return;
      }

      set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));
      markSettled(job.id);
      settledListeners.forEach((listener) => listener(job));
      if (job.status !== "failed") {
        setTimeout(() => get().dismiss(job.id), AUTO_DISMISS_MS);
      }

      return;
    }

    set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));

    // First snapshot of a job that is starting now — not one already running
    // when we connected (a seed) and not a re-seed of one we already track:
    // announce that work began.
    if (!prev && !seeding) {
      startedListeners.forEach((listener) => listener(job));
    }
  },

  // The feed stays the source of truth, so a failed cancel request is a no-op.
  cancel: (id) => cancelJob(id).catch(() => {}),

  dismiss: (id) => {
    set((s) => {
      if (!(id in s.jobs)) return s;
      const next = { ...s.jobs };
      delete next[id];
      return { jobs: next };
    });
  },
}));
