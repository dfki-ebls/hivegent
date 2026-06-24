import { create } from "zustand";

import { cancelJob, subscribeJobs } from "../lib/api";
import { TERMINAL_JOB_STATUSES, type JobView } from "../lib/types";

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
  /** Upsert a snapshot (from the feed or an immediate submit response). */
  upsert: (job: JobView) => void;
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
      try {
        await subscribeJobs(get().upsert);
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
  // retained terminal jobs on every reconnect, so upsert is idempotent and
  // order-independent: drop a stale snapshot, never resurrect a dismissed job,
  // and fire settle listeners only on the first transition into a terminal
  // state. A clean finish then lingers briefly; a failure stays until dismissed.
  upsert: (job) => {
    const prev = get().jobs[job.id];

    // A fast job's terminal snapshot (from the feed) can beat its queued
    // snapshot (from the submit response); never let the older one overwrite it.
    if (prev && job.updated_at < prev.updated_at) return;

    const terminal = isTerminal(job);
    const handled = settledJobIds.has(job.id);

    // Re-seeded after dismissal (terminal, handled, no longer shown): ignore it
    // so the tray does not flicker the job back in on a reconnect.
    if (terminal && handled && !prev) return;

    set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));

    if (!terminal || handled) return;

    markSettled(job.id);
    settledListeners.forEach((listener) => listener(job));
    if (job.status !== "failed") {
      setTimeout(() => get().dismiss(job.id), AUTO_DISMISS_MS);
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
