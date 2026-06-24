import { toast } from "sonner";
import { create } from "zustand";
import type {
  ReconvertDocumentOptions,
  UploadCollectionOptions,
  UploadDocumentOptions,
} from "../lib/api";
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
  uploadCollection,
  uploadDocument,
} from "../lib/api";
import type {
  DirectoryTreeResponse,
  DocumentInfo,
  JobView,
  LlmConfig,
  PipelineSpec,
} from "../lib/types";
import { isAbortError, treeDocuments } from "../lib/utils";
import { awaitJobSettled, onJobSettled, suppressJobToasts, useJobsStore } from "./jobs-store";

// The kind the upload route tags single-document jobs with; mirrors the backend
// `DocumentJobKind.UPLOAD` enum value, the cross-process contract for the feed.
const DOCUMENT_UPLOAD_KIND = "document.upload";

// Resolve a batch upload's single toast once its per-file jobs settle, mirroring
// their combined outcome, then lift the suppression that hid the per-file cues.
async function finalizeUploadToast(
  toastId: string,
  total: number,
  settlements: Promise<JobView | undefined>[],
  release: () => void,
): Promise<void> {
  try {
    const views = await Promise.all(settlements);
    const succeeded = views.filter((v) => v?.status === "succeeded").length;

    if (succeeded === total) {
      toast.success(`${total} documents added`, { id: toastId });
    } else if (succeeded === 0) {
      toast.error(`Failed to add ${total} documents`, { id: toastId });
    } else {
      toast.warning(`${succeeded} of ${total} documents added`, { id: toastId });
    }
  } finally {
    release();
  }
}

type Signalled<T> = T & { signal?: AbortSignal };

/** Per-scope document-management state. A scope is `~` (personal) or `@<group>`. */
export interface ScopeState {
  documents: DocumentInfo[];
  directoryTree: DirectoryTreeResponse | null;
  mutatingPaths: Set<string>;
  isUploading: boolean;
  hasFetched: boolean;
  error: string | null;
}

export const DEFAULT_SCOPE_STATE: ScopeState = {
  documents: [],
  directoryTree: null,
  mutatingPaths: new Set(),
  isUploading: false,
  hasFetched: false,
  error: null,
};

interface DocumentsStore {
  byScope: Record<string, ScopeState>;
  refresh: (scope: string) => Promise<void>;
  upload: (scope: string, file: File, options?: Signalled<UploadDocumentOptions>) => Promise<void>;
  uploadMultiple: (
    scope: string,
    files: File[],
    options?: Signalled<UploadDocumentOptions>,
  ) => Promise<void>;
  uploadCol: (
    scope: string,
    file: File,
    options?: Signalled<UploadCollectionOptions>,
  ) => Promise<void>;
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

    // Uploads are connection-bound only while the bytes transfer; the PUT
    // returns a job that converts and indexes server-side, so `isUploading`
    // covers the brief send and the job tray shows the rest.
    upload: async (scope, file, options) => {
      patch(scope, { isUploading: true, error: null });
      try {
        const job = await uploadDocument(canonicalPath(scope, file.name), file, options);
        useJobsStore.getState().upsert(job);
      } catch (err) {
        if (!isAbortError(err)) {
          patch(scope, { error: err instanceof Error ? err.message : "Upload failed" });
        }
      } finally {
        patch(scope, { isUploading: false });
      }
    },

    // Submit each file sequentially as its own tray job; the tray shows the
    // per-file conversion, so the store only flags the brief send and reports
    // any submits that never became a job. Aborting stops the remaining sends.
    // One consolidated toast stands in for the per-file jobs: its toasts are
    // suppressed up front (so the suppression cannot race their first feed
    // snapshot) and this toast resolves once they all settle.
    uploadMultiple: async (scope, files, options) => {
      const signal = options?.signal;
      patch(scope, { isUploading: true, error: null });

      const toastId = crypto.randomUUID();
      const release = suppressJobToasts(
        (job) => job.scope === scope && job.kind === DOCUMENT_UPLOAD_KIND,
      );
      toast.loading(`Processing ${files.length} documents`, { id: toastId });

      const settlements: Promise<JobView | undefined>[] = [];
      const failed: string[] = [];
      try {
        await files.reduce(
          (chain, file) =>
            chain.then(async () => {
              if (signal?.aborted) return;
              try {
                const job = await uploadDocument(canonicalPath(scope, file.name), file, options);
                settlements.push(awaitJobSettled(job.id));
                useJobsStore.getState().upsert(job);
              } catch (err) {
                if (!isAbortError(err)) failed.push(file.name);
              }
            }),
          Promise.resolve(),
        );
      } finally {
        patch(scope, {
          isUploading: false,
          ...(failed.length > 0 && {
            error: `Failed to upload ${failed.length} file${failed.length > 1 ? "s" : ""}: ${failed.join(", ")}`,
          }),
        });
      }

      void finalizeUploadToast(toastId, files.length, settlements, release);
    },

    uploadCol: async (scope, file, options) => {
      patch(scope, { isUploading: true, error: null });
      try {
        const job = await uploadCollection(scope, file, options);
        useJobsStore.getState().upsert(job);
      } catch (err) {
        if (!isAbortError(err)) {
          patch(scope, { error: err instanceof Error ? err.message : "Collection upload failed" });
        }
        throw err;
      } finally {
        patch(scope, { isUploading: false });
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
