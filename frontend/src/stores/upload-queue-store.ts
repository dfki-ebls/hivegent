import { create } from "zustand";

import {
  type UploadCollectionOptions,
  canonicalPath,
  uploadCollection,
  uploadDocument,
} from "../lib/api";
import { errorMessage, fileStem, isAbortError } from "../lib/utils";
import { useJobsStore } from "./jobs-store";

// How many files transfer their bytes at once. Conversion and indexing run
// server-side behind the backend's own job semaphore, so this caps concurrent
// uploads only, not the processing that follows.
const UPLOAD_CONCURRENCY = 3;

export type UploadItemStatus =
  | "preparing" // building a collection archive (collections only)
  | "queued" // ready, waiting for a free upload slot
  | "uploading" // bytes transferring to the backend
  | "failed"; // the bytes never reached a job (e.g. a network error), the user can retry

/** Spec and LLM options carried with an upload, shared with the API layer. */
export type UploadOptions = UploadCollectionOptions;

/** One file or collection moving through the upload queue. */
export interface UploadItem {
  id: string;
  scope: string;
  name: string;
  stem: string;
  kind: "file" | "collection";
  status: UploadItemStatus;
  error: string | null;
  // Whether a failed item can be re-run from the tray. False for a pre-queue
  // failure (e.g. an unreadable drop) that has no retained work to retry.
  retryable: boolean;
  // Enqueue time, so the unified task list can interleave items with the jobs
  // they hand off to (which are ordered by their own created_at).
  createdAt: number;
}

// Runtime handles kept out of the rendered store so mutating them never causes a
// re-render: the abort controller for an item's in-flight prepare or upload, and
// the work needed to (re)run it.
interface ItemWork {
  scope: string;
  kind: "file" | "collection";
  options: UploadOptions;
  file: File | null; // set once a collection finishes preparing
  build: ((signal: AbortSignal) => Promise<File>) | null; // collection prepare step
}

const controllers = new Map<string, AbortController>();
const work = new Map<string, ItemWork>();
let active = 0;

type UploadListener = (item: UploadItem) => void;

const addedListeners = new Set<UploadListener>();

/**
 * Register a callback fired once for each item entering the queue — an enqueued
 * upload or a reported pre-queue failure. Mirrors jobs-store's onJobStarted so
 * the tray can pop open on any new entry from either source. Returns an
 * unsubscribe function.
 */
export function onUploadAdded(listener: UploadListener): () => void {
  addedListeners.add(listener);
  return () => {
    addedListeners.delete(listener);
  };
}

// Statuses an item holds while its work is still local: they occupy a stem (so a
// re-drop is skipped rather than racing the original into the backend) and mean
// the work would be lost on reload.
const ACTIVE_STATUSES: ReadonlySet<UploadItemStatus> = new Set([
  "preparing",
  "queued",
  "uploading",
]);

interface UploadQueueStore {
  items: Record<string, UploadItem>;
  enqueueFiles: (scope: string, files: File[], options: UploadOptions) => void;
  enqueueCollection: (
    scope: string,
    name: string,
    build: (signal: AbortSignal) => Promise<File>,
    options: UploadOptions,
  ) => void;
  report: (scope: string, name: string, error: string) => void;
  retry: (id: string) => void;
  cancel: (id: string) => void;
  dismiss: (id: string) => void;
}

export const useUploadQueue = create<UploadQueueStore>((set, get) => {
  const patchItem = (id: string, delta: Partial<UploadItem>): void =>
    set((s) => {
      const item = s.items[id];
      if (!item) return s;

      return { items: { ...s.items, [id]: { ...item, ...delta } } };
    });

  const addItem = (item: UploadItem): void => {
    set((s) => ({ items: { ...s.items, [item.id]: item } }));
    addedListeners.forEach((listener) => listener(item));
  };

  // A fresh queue item with its constant defaults filled in. A pre-queue failure
  // overrides those defaults to drop in already-failed and non-retryable.
  const makeItem = (
    base: Pick<UploadItem, "id" | "scope" | "name" | "stem" | "kind" | "status">,
    overrides?: Partial<Pick<UploadItem, "error" | "retryable">>,
  ): UploadItem => ({
    ...base,
    error: overrides?.error ?? null,
    retryable: overrides?.retryable ?? true,
    createdAt: Date.now(),
  });

  const removeItem = (id: string): void => {
    controllers.delete(id);
    work.delete(id);
    set((s) => {
      if (!(id in s.items)) return s;
      const next = { ...s.items };
      delete next[id];
      return { items: next };
    });
  };

  // Stems currently held by an active item in `scope`, so a fresh batch can skip
  // duplicates in one pass instead of rescanning every item per file.
  const occupiedStems = (scope: string): Set<string> =>
    new Set(
      Object.values(get().items)
        .filter((i) => i.scope === scope && ACTIVE_STATUSES.has(i.status))
        .map((i) => i.stem),
    );

  // Start queued items until the concurrency cap is reached. runItem flips an
  // item out of "queued" before its first await, so a re-entrant pump never
  // double-starts the same item.
  function pump(): void {
    for (const item of Object.values(get().items)) {
      if (active >= UPLOAD_CONCURRENCY) break;
      if (item.status === "queued") void runItem(item.id);
    }
  }

  // Run a cancellable step under a fresh controller registered for the item,
  // mapping an abort to removal and any other failure to a failed item. Shared
  // by the two transfers an item makes: building its archive and uploading it.
  async function withController(
    id: string,
    run: (signal: AbortSignal) => Promise<void>,
  ): Promise<void> {
    const ctrl = new AbortController();
    controllers.set(id, ctrl);

    try {
      await run(ctrl.signal);
    } catch (err) {
      if (isAbortError(err)) removeItem(id);
      else patchItem(id, { status: "failed", error: errorMessage(err) });
    } finally {
      controllers.delete(id);
    }
  }

  // Drive one queued item through its upload: transfer the bytes, hand the
  // resulting job to the tray, and drop the item so the job row takes over.
  async function runItem(id: string): Promise<void> {
    const w = work.get(id);

    if (!w?.file || get().items[id]?.status !== "queued") return;

    const file = w.file;
    active += 1;
    patchItem(id, { status: "uploading", error: null });

    await withController(id, async (signal) => {
      const job =
        w.kind === "collection"
          ? await uploadCollection(w.scope, file, { ...w.options, signal })
          : await uploadDocument(canonicalPath(w.scope, file.name), file, { ...w.options, signal });
      useJobsStore.getState().upsert(job);
      removeItem(id);
    });

    active -= 1;
    pump();
  }

  // Build a collection's archive before it joins the queue proper. Runs off the
  // concurrency cap because it is local CPU work, not a transfer.
  async function prepareCollection(id: string): Promise<void> {
    const w = work.get(id);

    if (!w?.build) return;

    const build = w.build;

    await withController(id, async (signal) => {
      work.set(id, { ...w, file: await build(signal) });
      patchItem(id, { status: "queued" });
    });

    pump();
  }

  return {
    items: {},

    // Append each file as its own queue item. A new call never touches items
    // already in flight, so dropping more files while others upload accumulates
    // work instead of cancelling it. A file whose stem is already queued is
    // skipped silently; its in-flight item already stands in for it in the tray.
    enqueueFiles: (scope, files, options) => {
      const occupied = occupiedStems(scope);
      for (const file of files) {
        const stem = fileStem(file.name);

        if (occupied.has(stem)) continue;

        occupied.add(stem);
        const id = crypto.randomUUID();
        work.set(id, { scope, kind: "file", options, file, build: null });
        addItem(makeItem({ id, scope, name: file.name, stem, kind: "file", status: "queued" }));
      }

      pump();
    },

    enqueueCollection: (scope, name, build, options) => {
      const id = crypto.randomUUID();
      work.set(id, { scope, kind: "collection", options, file: null, build });
      addItem(makeItem({ id, scope, name, stem: name, kind: "collection", status: "preparing" }));
      void prepareCollection(id);
    },

    // Surface a failure that occurred before any work could be queued (e.g. a
    // drop we could not read) as a dismiss-only tray row, so it is as visible as
    // every other task instead of being lost to the console. It holds no work,
    // so it cannot be retried and never blocks a stem.
    report: (scope, name, error) => {
      const id = crypto.randomUUID();
      const base = {
        id,
        scope,
        name,
        stem: fileStem(name),
        kind: "file",
        status: "failed",
      } as const;
      addItem(makeItem(base, { error, retryable: false }));
    },

    retry: (id) => {
      const item = get().items[id];

      if (item?.status !== "failed") return;

      const w = work.get(id);

      if (w?.kind === "collection" && !w.file) {
        patchItem(id, { status: "preparing", error: null });
        void prepareCollection(id);
      } else {
        patchItem(id, { status: "queued", error: null });
        pump();
      }
    },

    // Abort the in-flight transfer (if any) and drop the item. Aborting an
    // uploading item also rejects its request, whose handler is a no-op once the
    // item is gone.
    cancel: (id) => {
      controllers.get(id)?.abort();
      removeItem(id);
    },

    dismiss: (id) => removeItem(id),
  };
});

/** True while any upload is still local, i.e. its work would be lost on reload. */
export const selectHasPendingUploads = (s: UploadQueueStore): boolean =>
  Object.values(s.items).some((i) => ACTIVE_STATUSES.has(i.status));
