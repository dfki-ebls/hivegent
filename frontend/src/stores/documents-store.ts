import { create } from "zustand";
import type { ReconvertDocumentOptions } from "@/lib/api";
import {
  type BulkMoveEntry,
  bulkDelete as apiBulkDelete,
  bulkMove as apiBulkMove,
  bulkRechunk as apiBulkRechunk,
  bulkReconvert as apiBulkReconvert,
  canonicalPath,
  createDirectory,
  deleteDirectory,
  deleteDocument,
  getDirectories,
  moveDirectory,
  moveDocument,
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
import { treeDocuments, withDirectory } from "@/lib/utils";
import { awaitJobSettled, onJobSettled, useJobsStore } from "@/stores/jobs-store";

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

interface DocumentsStore {
  byScope: Record<string, ScopeState>;
  refresh: (scope: string) => Promise<void>;
  remove: (scope: string, filename: string) => Promise<void>;
  rechunk: (scope: string, filename: string, spec?: PipelineSpec) => Promise<void>;
  reconvert: (scope: string, filename: string, options?: ReconvertDocumentOptions) => Promise<void>;
  bulkRechunk: (scope: string, files: string[], spec?: PipelineSpec) => Promise<void>;
  bulkReconvert: (
    scope: string,
    files: string[],
    spec?: PipelineSpec,
    llm?: LlmConfig,
  ) => Promise<void>;
  bulkDelete: (scope: string, files: string[]) => Promise<void>;
  // A move may cross workspaces, so source and destination scopes are distinct
  // (they coincide for an in-workspace move). Local paths are relative to their
  // own scope; both scopes refresh once the move settles.
  bulkMove: (srcScope: string, destScope: string, moves: BulkMoveEntry[]) => Promise<void>;
  move: (
    srcScope: string,
    filepath: string,
    destScope: string,
    destination: string,
  ) => Promise<void>;
  createDir: (scope: string, path: string) => Promise<void>;
  deleteDir: (scope: string, path: string) => Promise<void>;
  moveDir: (
    srcScope: string,
    source: string,
    destScope: string,
    destination: string,
  ) => Promise<void>;
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

  /** Run a single-path mutation with shared mutating-path tracking and refresh.
   *
   * The mutating spinner and any error live on the source scope (where `path`
   * is shown). `alsoRefresh` names a second scope to reload on completion — the
   * destination of a cross-workspace move, which gains the entry the source
   * loses. */
  const withMutating = async (
    scope: string,
    path: string,
    errorMsg: string,
    operation: () => Promise<unknown>,
    alsoRefresh?: string,
  ): Promise<void> => {
    patch(scope, (s) => ({ mutatingPaths: new Set(s.mutatingPaths).add(path), error: null }));
    try {
      await operation();
    } catch (err) {
      patch(scope, { error: err instanceof Error ? err.message : errorMsg });
    } finally {
      // Refresh even after a failure so a stale view (e.g. an entry the
      // backend no longer knows about) converges with the server state. The two
      // scopes are separate workspaces, so their walks run concurrently.
      const scopes = alsoRefresh && alsoRefresh !== scope ? [scope, alsoRefresh] : [scope];
      await Promise.all(scopes.map((s) => silentRefresh(s).catch(() => {})));
      patch(scope, (s) => {
        const next = new Set(s.mutatingPaths);
        next.delete(path);
        return { mutatingPaths: next };
      });
    }
  };

  // Submit a background job: the tray shows its progress and the job-settle
  // handler refreshes the job's scope, so the store only has to record the new
  // job (or surface a submit failure). Shared by the bulk operations.
  //
  // `alsoRefresh` names a second scope to reload once the job settles — the
  // destination of a cross-workspace bulk move, which the job's own scope (the
  // source) does not cover.
  const submitJob = async (
    scope: string,
    errorMsg: string,
    submit: () => Promise<JobView>,
    alsoRefresh?: string,
  ): Promise<void> => {
    patch(scope, { error: null });
    try {
      const job = await submit();
      useJobsStore.getState().upsert(job);
      if (alsoRefresh && alsoRefresh !== scope) {
        void awaitJobSettled(job.id).then(() => {
          void useDocumentsStore.getState().refresh(alsoRefresh);
        });
      }
    } catch (err) {
      patch(scope, { error: err instanceof Error ? err.message : errorMsg });
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
          error: err instanceof Error ? err.message : "Failed to load documents",
          hasFetched: true,
        });
      }
    },

    remove: (scope, filename) =>
      withMutating(scope, filename, "Delete failed", () =>
        deleteDocument(canonicalPath(scope, filename)),
      ),

    // Rechunk runs as a background job; the tray shows its progress. The promise
    // resolves once the job settles so a caller that needs the fresh chunks (the
    // document dialog) can refetch, while the work itself runs off the request.
    rechunk: async (scope, filename, spec) => {
      patch(scope, { error: null });
      try {
        await submitAndAwait(() => rechunkDocument(canonicalPath(scope, filename), spec));
      } catch (err) {
        patch(scope, { error: err instanceof Error ? err.message : "Rechunk failed" });
      }
    },

    // Reconvert runs as a background job (the tray surfaces its progress), but
    // the targeted row also spins until the job settles so the document visibly
    // reflects that it is being reprocessed.
    reconvert: (scope, filename, options) =>
      withMutating(scope, filename, "Reconvert failed", () =>
        submitAndAwait(() => reconvertDocument(canonicalPath(scope, filename), options)),
      ),

    bulkRechunk: (scope, files, spec) =>
      submitJob(scope, "Bulk rechunk failed", () =>
        apiBulkRechunk(
          files.map((f) => canonicalPath(scope, f)),
          spec,
        ),
      ),

    bulkReconvert: (scope, files, spec, llm) =>
      submitJob(scope, "Bulk reconvert failed", () =>
        apiBulkReconvert(
          files.map((f) => canonicalPath(scope, f)),
          spec,
          llm,
        ),
      ),

    bulkDelete: (scope, files) =>
      submitJob(scope, "Bulk delete failed", () =>
        apiBulkDelete(files.map((f) => canonicalPath(scope, f))),
      ),

    bulkMove: (srcScope, destScope, moves) =>
      submitJob(
        srcScope,
        "Bulk move failed",
        () =>
          apiBulkMove(
            moves.map(({ source, destination }) => ({
              source: canonicalPath(srcScope, source),
              destination: canonicalPath(destScope, destination),
            })),
          ),
        destScope,
      ),

    move: (srcScope, filepath, destScope, destination) =>
      withMutating(
        srcScope,
        filepath,
        "Move failed",
        () =>
          moveDocument(canonicalPath(srcScope, filepath), canonicalPath(destScope, destination)),
        destScope,
      ),

    // Grafted into the local tree the moment the POST returns, so the new
    // directory appears immediately instead of after the refresh's
    // full-workspace walk, which then only reconciles whatever else moved.
    createDir: (scope, path) =>
      withMutating(scope, path, "Failed to create directory", async () => {
        await createDirectory(canonicalPath(scope, path));
        patch(scope, (s) =>
          s.directoryTree
            ? {
                directoryTree: {
                  ...s.directoryTree,
                  root: withDirectory(s.directoryTree.root, path),
                },
              }
            : {},
        );
      }),

    deleteDir: (scope, path) =>
      withMutating(scope, path, "Failed to delete directory", () =>
        deleteDirectory(canonicalPath(scope, path)),
      ),

    moveDir: (srcScope, source, destScope, destination) =>
      withMutating(
        srcScope,
        source,
        "Failed to move directory",
        () => moveDirectory(canonicalPath(srcScope, source), canonicalPath(destScope, destination)),
        destScope,
      ),

    clearError: (scope) => patch(scope, { error: null }),
  };
});

// A settled document job may have changed its scope on disk: a success adds or
// reconverts an entry, a failed/cancelled one drops the entry it had reserved,
// and a bulk op moves or deletes many. Either way the view can be stale, so
// refresh the scope on every terminal document job, not just successes.
onJobSettled((job) => {
  if (job.scope && job.kind.startsWith("document.")) {
    void useDocumentsStore.getState().refresh(job.scope);
  }
});
