import { create } from "zustand";
import type { ReconvertDocumentOptions } from "@/lib/api";
import {
  applyChanges,
  bulkRechunk as apiBulkRechunk,
  bulkReconvert as apiBulkReconvert,
  type ChangeOperation,
  canonicalPath,
  type DeleteKind,
  getDirectories,
  rechunkDocument,
  reconvertDocument,
} from "@/lib/api";
import type {
  DirectoryTreeResponse,
  DocumentInfo,
  JobView,
  LlmConfig,
  PipelineSpec,
} from "@/lib/types";
import { errorMessage, treeDocuments, withDirectory } from "@/lib/utils";
import {
  awaitJobSettled,
  onFeedReady,
  onJobSettled,
  onWorkspaceChanged,
  publishWorkspaceChanged,
  useJobsStore,
} from "@/stores/jobs-store";

/** Per-scope document-management state. A scope is `~` (personal) or `@<group>`. */
export interface ScopeState {
  documents: DocumentInfo[];
  directoryTree: DirectoryTreeResponse | null;
  mutatingPaths: Set<string>;
  hasFetched: boolean;
  error: string | null;
}

export const DEFAULT_SCOPE_STATE: ScopeState = {
  documents: [],
  directoryTree: null,
  mutatingPaths: new Set(),
  hasFetched: false,
  error: null,
};

/** A move of one document or directory, each path local to its own scope. */
export interface LocalMove {
  source: string;
  destination: string;
}

interface DocumentsStore {
  byScope: Record<string, ScopeState>;
  refresh: (scope: string) => Promise<void>;
  // Deletes documents (`entry`) or directories (`dir`), refused for the other kind.
  remove: (scope: string, paths: string[], expect: DeleteKind) => Promise<void>;
  rechunk: (scope: string, filename: string, spec?: PipelineSpec) => Promise<void>;
  reconvert: (scope: string, filename: string, options?: ReconvertDocumentOptions) => Promise<void>;
  bulkRechunk: (scope: string, files: string[], spec?: PipelineSpec) => Promise<void>;
  bulkReconvert: (
    scope: string,
    files: string[],
    spec?: PipelineSpec,
    llm?: LlmConfig,
  ) => Promise<void>;
  // A move may cross workspaces, so source and destination scopes are distinct
  // (they coincide for an in-workspace move). It covers documents and
  // directories alike, and both scopes refresh once it settles.
  move: (srcScope: string, destScope: string, moves: LocalMove[]) => Promise<void>;
  createDir: (scope: string, path: string) => Promise<void>;
  clearError: (scope: string) => void;
}

export const useDocumentsStore = create<DocumentsStore>((set) => {
  const patch = (
    scope: string,
    update: Partial<ScopeState> | ((s: ScopeState) => Partial<ScopeState>),
  ) =>
    set((store) => {
      const current = store.byScope[scope] ?? DEFAULT_SCOPE_STATE;
      const delta = typeof update === "function" ? update(current) : update;
      return { byScope: { ...store.byScope, [scope]: { ...current, ...delta } } };
    });

  const fetchTree = async (scope: string) => {
    const directoryTree = await getDirectories(scope);
    patch(scope, { documents: treeDocuments(directoryTree.root), directoryTree, hasFetched: true });
  };

  // At most one tree read in flight per scope, plus one queued follow-up. The
  // endpoint walks the entire workspace, so a burst — a batch of settling upload
  // jobs, a multi-select delete, a mount effect racing a post-mutation refresh —
  // would otherwise pay for a full walk per event. A caller arriving mid-read
  // cannot reuse that read (it may predate the caller's own mutation), but all
  // such callers can share one follow-up, since every read returns the whole
  // tree. Serialising this way also means two reads can never resolve out of
  // order and overwrite each other.
  const active: Record<string, Promise<void> | undefined> = {};
  const followUp: Record<string, Promise<void> | undefined> = {};

  const silentRefresh = (scope: string): Promise<void> => {
    const current = active[scope];
    if (!current) {
      return (active[scope] = fetchTree(scope).finally(() => {
        delete active[scope];
      }));
    }

    // `current` settles only after its own `finally` has cleared `active`, so
    // the recursive call always starts a genuinely fresh read.
    return (followUp[scope] ??= current
      .catch(() => {})
      .then(() => {
        delete followUp[scope];
        return silentRefresh(scope);
      }));
  };

  /** Run a mutation with shared mutating-path tracking and refresh.
   *
   * The mutating spinners and any error live on the source scope (where `paths`
   * are shown). A batch is one request that lands whole or not at all, so its
   * single error stands for every path. The spinners stay until the scope's
   * latest read lands, the one the change itself started through
   * {@link refreshIfLoaded}, or a fresh one. */
  const withMutating = async (
    scope: string,
    paths: readonly string[],
    operation: () => Promise<unknown>,
  ): Promise<void> => {
    patch(scope, (s) => ({ mutatingPaths: new Set([...s.mutatingPaths, ...paths]), error: null }));
    try {
      await operation();
    } catch (err) {
      patch(scope, { error: errorMessage(err) });
    } finally {
      // Refresh even after a failure so a stale view (e.g. an entry the
      // backend no longer knows about) converges with the server state.
      await (followUp[scope] ?? active[scope] ?? silentRefresh(scope)).catch(() => {});
      patch(scope, (s) => ({
        mutatingPaths: new Set([...s.mutatingPaths].filter((p) => !paths.includes(p))),
      }));
    }
  };

  // Apply workspace changes as one request. Once it succeeded, the backend's
  // `WorkspaceChanged` is published for this tab, since its own feed skips the
  // change and what was sent is not what applied, so the scopes it names
  // refresh and the paths this tab keeps follow, and `onApplied` runs.
  const change = (
    scope: string,
    paths: readonly string[],
    operations: ChangeOperation[],
    onApplied?: () => void,
  ): Promise<void> =>
    withMutating(scope, paths, async () => {
      publishWorkspaceChanged(await applyChanges(operations));
      onApplied?.();
    });

  // Submit a background job: the tray shows its progress and the job-settle
  // handler refreshes the job's scope, so the store only has to record the new
  // job (or surface a submit failure). Shared by bulk rechunk and reconvert.
  const submitJob = async (scope: string, submit: () => Promise<JobView>): Promise<void> => {
    patch(scope, { error: null });
    try {
      useJobsStore.getState().upsert(await submit());
    } catch (err) {
      patch(scope, { error: errorMessage(err) });
    }
  };

  // Submit a job, record it, and resolve once it settles — for the callers
  // (reconvert, the dialog's rechunk) that refresh inline on completion while
  // the work itself runs off the request.
  const submitAndAwait = async (submit: () => Promise<JobView>): Promise<void> => {
    const job = await submit();
    useJobsStore.getState().upsert(job);
    await awaitJobSettled(job.id);
  };

  return {
    byScope: {},

    refresh: async (scope) => {
      try {
        await silentRefresh(scope);
      } catch (err) {
        patch(scope, {
          error: errorMessage(err),
          hasFetched: true,
        });
      }
    },

    remove: (scope, paths, expect) =>
      change(
        scope,
        paths,
        paths.map((path) => ({ kind: "delete", path: canonicalPath(scope, path), expect })),
      ),

    // Rechunk runs as a background job; the tray shows its progress. The promise
    // resolves once the job settles so a caller that needs the fresh chunks (the
    // document dialog) can refetch, while the work itself runs off the request.
    rechunk: async (scope, filename, spec) => {
      patch(scope, { error: null });
      try {
        await submitAndAwait(() => rechunkDocument(canonicalPath(scope, filename), spec));
      } catch (err) {
        patch(scope, { error: errorMessage(err) });
      }
    },

    // Reconvert runs as a background job (the tray surfaces its progress), but
    // the targeted row also spins until the job settles so the document visibly
    // reflects that it is being reprocessed.
    reconvert: (scope, filename, options) =>
      withMutating(scope, [filename], () =>
        submitAndAwait(() => reconvertDocument(canonicalPath(scope, filename), options)),
      ),

    bulkRechunk: (scope, files, spec) =>
      submitJob(scope, () =>
        apiBulkRechunk(
          files.map((f) => canonicalPath(scope, f)),
          spec,
        ),
      ),

    bulkReconvert: (scope, files, spec, llm) =>
      submitJob(scope, () =>
        apiBulkReconvert(
          files.map((f) => canonicalPath(scope, f)),
          spec,
          llm,
        ),
      ),

    move: (srcScope, destScope, moves) =>
      change(
        srcScope,
        moves.map(({ source }) => source),
        moves.map(({ source, destination }) => ({
          kind: "move",
          source: canonicalPath(srcScope, source),
          destination: canonicalPath(destScope, destination),
        })),
      ),

    // Grafted into the local tree the moment the request returns, so the new
    // directory appears immediately instead of after the refresh's
    // full-workspace walk, which then only reconciles whatever else moved.
    createDir: (scope, path) =>
      change(scope, [path], [{ kind: "mkdir", path: canonicalPath(scope, path) }], () =>
        patch(scope, (s) =>
          s.directoryTree
            ? {
                directoryTree: {
                  ...s.directoryTree,
                  root: withDirectory(s.directoryTree.root, path),
                },
              }
            : {},
        ),
      ),

    clearError: (scope) => patch(scope, { error: null }),
  };
});

// Reload a scope the server reports as changed, but only one this store has
// already read: an untouched scope has nothing to reconcile, and the section
// that displays it fetches on mount, so a chat-only session never pays for the
// full-workspace walk a refresh costs.
const refreshIfLoaded = (scope: string) => {
  if (useDocumentsStore.getState().byScope[scope]?.hasFetched) {
    void useDocumentsStore.getState().refresh(scope);
  }
};

// A settled document job may have changed its scope on disk: a success adds or
// reconverts an entry, a failed/cancelled one drops the entry it had reserved,
// and a bulk op reprocesses many. Either way the view can be stale, so
// refresh the scope on every terminal document job, not just successes.
onJobSettled((job) => {
  if (job.scope && job.kind.startsWith("document.")) refreshIfLoaded(job.scope);
});

// The same need for a mutation that ran inline and so never was a job.
onWorkspaceChanged(({ scopes }) => scopes.forEach(refreshIfLoaded));

// Workspace-change events are transient, so reconcile loaded scopes after every
// feed handshake to cover mutations that occurred while it was disconnected.
onFeedReady(() => Object.keys(useDocumentsStore.getState().byScope).forEach(refreshIfLoaded));
