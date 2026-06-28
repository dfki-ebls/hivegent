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
import { treeDocuments } from "@/lib/utils";
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
  bulkMove: (scope: string, moves: BulkMoveEntry[]) => Promise<void>;
  move: (scope: string, filepath: string, destination: string) => Promise<void>;
  createDir: (scope: string, path: string) => Promise<void>;
  deleteDir: (scope: string, path: string) => Promise<void>;
  moveDir: (scope: string, source: string, destination: string) => Promise<void>;
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

  // Monotonic per-scope refresh tokens: concurrent refreshes (e.g. a mount
  // effect racing a post-mutation refresh) may resolve out of order, and an
  // older response must not overwrite a newer one.
  const refreshSeq: Record<string, number> = {};

  const silentRefresh = async (scope: string) => {
    const seq = (refreshSeq[scope] ?? 0) + 1;
    refreshSeq[scope] = seq;
    const directoryTree = await getDirectories(scope);
    if (refreshSeq[scope] !== seq) return;
    patch(scope, { documents: treeDocuments(directoryTree.root), directoryTree, hasFetched: true });
  };

  /** Run a single-path mutation with shared mutating-path tracking and refresh. */
  const withMutating = async (
    scope: string,
    path: string,
    errorMsg: string,
    operation: () => Promise<unknown>,
  ): Promise<void> => {
    patch(scope, (s) => ({ mutatingPaths: new Set(s.mutatingPaths).add(path), error: null }));
    try {
      await operation();
    } catch (err) {
      patch(scope, { error: err instanceof Error ? err.message : errorMsg });
    } finally {
      // Refresh even after a failure so a stale view (e.g. an entry the
      // backend no longer knows about) converges with the server state.
      await silentRefresh(scope).catch(() => {});
      patch(scope, (s) => {
        const next = new Set(s.mutatingPaths);
        next.delete(path);
        return { mutatingPaths: next };
      });
    }
  };

  // Submit a background job: the tray shows its progress and the job-settle
  // handler refreshes the scope, so the store only has to record the new job
  // (or surface a submit failure). Shared by the bulk operations.
  const submitJob = async (
    scope: string,
    errorMsg: string,
    submit: () => Promise<JobView>,
  ): Promise<void> => {
    patch(scope, { error: null });
    try {
      useJobsStore.getState().upsert(await submit());
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

    bulkMove: (scope, moves) =>
      submitJob(scope, "Bulk move failed", () =>
        apiBulkMove(
          moves.map(({ source, destination }) => ({
            source: canonicalPath(scope, source),
            destination: canonicalPath(scope, destination),
          })),
        ),
      ),

    move: (scope, filepath, destination) =>
      withMutating(scope, filepath, "Move failed", () =>
        moveDocument(canonicalPath(scope, filepath), canonicalPath(scope, destination)),
      ),

    createDir: (scope, path) =>
      withMutating(scope, path, "Failed to create directory", () =>
        createDirectory(canonicalPath(scope, path)),
      ),

    deleteDir: (scope, path) =>
      withMutating(scope, path, "Failed to delete directory", () =>
        deleteDirectory(canonicalPath(scope, path)),
      ),

    moveDir: (scope, source, destination) =>
      withMutating(scope, source, "Failed to move directory", () =>
        moveDirectory(canonicalPath(scope, source), canonicalPath(scope, destination)),
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
