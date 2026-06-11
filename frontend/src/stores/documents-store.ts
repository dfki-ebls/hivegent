import { create } from "zustand";
import type {
  ReconvertDocumentOptions,
  UploadCollectionOptions,
  UploadDocumentOptions,
} from "../lib/api";
import {
  bulkDeleteStream,
  bulkRechunkStream,
  bulkReconvertStream,
  canonicalPath,
  createDirectory,
  deleteDirectory,
  deleteDocument,
  getDirectories,
  moveDirectory,
  moveDocument,
  rechunkDocumentStream,
  reconvertDocumentStream,
  uploadCollectionStream,
  uploadDocumentStream,
} from "../lib/api";
import type {
  CollectionUploadResponse,
  DirectoryTreeResponse,
  DocumentInfo,
  LlmConfig,
  OperationStage,
  PipelineSpec,
  UploadProgress,
} from "../lib/types";
import { isAbortError, treeDocuments } from "../lib/utils";

type Signalled<T> = T & { signal?: AbortSignal };

/** Per-scope document-management state. A scope is `~` (personal) or `@<group>`. */
export interface ScopeState {
  documents: DocumentInfo[];
  directoryTree: DirectoryTreeResponse | null;
  mutatingPaths: Set<string>;
  isUploading: boolean;
  hasFetched: boolean;
  uploadProgress: UploadProgress | null;
  bulkProgress: UploadProgress | null;
  operationStage: OperationStage | null;
  error: string | null;
}

export const DEFAULT_SCOPE_STATE: ScopeState = {
  documents: [],
  directoryTree: null,
  mutatingPaths: new Set(),
  isUploading: false,
  hasFetched: false,
  uploadProgress: null,
  bulkProgress: null,
  operationStage: null,
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
  ) => Promise<CollectionUploadResponse>;
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
  move: (scope: string, filepath: string, destination: string) => Promise<void>;
  createDir: (scope: string, path: string) => Promise<void>;
  deleteDir: (scope: string, path: string) => Promise<void>;
  moveDir: (scope: string, source: string, destination: string) => Promise<void>;
  clearError: (scope: string) => void;
}

export const useDocumentsStore = create<DocumentsStore>((set, get) => {
  const scopeState = (scope: string): ScopeState => get().byScope[scope] ?? DEFAULT_SCOPE_STATE;

  const patch = (
    scope: string,
    update: Partial<ScopeState> | ((s: ScopeState) => Partial<ScopeState>),
  ) =>
    set((store) => {
      const current = store.byScope[scope] ?? DEFAULT_SCOPE_STATE;
      const delta = typeof update === "function" ? update(current) : update;
      return { byScope: { ...store.byScope, [scope]: { ...current, ...delta } } };
    });

  const onStage = (scope: string) => (stage: OperationStage) => {
    const current = scopeState(scope).operationStage;
    if (current?.stage === stage.stage && current?.detail === stage.detail) return;
    patch(scope, { operationStage: stage });
  };

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
      await silentRefresh(scope);
    } catch (err) {
      patch(scope, { error: err instanceof Error ? err.message : errorMsg });
    } finally {
      patch(scope, (s) => {
        const next = new Set(s.mutatingPaths);
        next.delete(path);
        return { mutatingPaths: next, operationStage: null };
      });
    }
  };

  /** Run a bulk operation with shared progress tracking and refresh. */
  const runBulk = async (
    scope: string,
    errorMsg: string,
    operation: (onProgress: (p: UploadProgress) => void) => Promise<unknown>,
  ): Promise<void> => {
    patch(scope, { bulkProgress: null, error: null });
    try {
      await operation((bulkProgress) => patch(scope, { bulkProgress }));
      await silentRefresh(scope);
    } catch (err) {
      patch(scope, { error: err instanceof Error ? err.message : errorMsg });
    } finally {
      patch(scope, { bulkProgress: null });
    }
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

    upload: async (scope, file, options) => {
      patch(scope, { isUploading: true, operationStage: null, error: null });
      try {
        await uploadDocumentStream(canonicalPath(scope, file.name), file, {
          ...options,
          onStage: onStage(scope),
        });
        await silentRefresh(scope);
      } catch (err) {
        if (!isAbortError(err)) {
          patch(scope, { error: err instanceof Error ? err.message : "Upload failed" });
        }
      } finally {
        patch(scope, { isUploading: false, operationStage: null });
      }
    },

    uploadMultiple: async (scope, files, options) => {
      const signal = options?.signal;
      patch(scope, { isUploading: true, error: null, uploadProgress: null });
      let failedSnapshot: string[] = [];
      try {
        for (let i = 0; i < files.length; i++) {
          if (signal?.aborted) break;
          const file = files[i];
          patch(scope, {
            uploadProgress: {
              current: i,
              total: files.length,
              currentFile: file.name,
              failedFiles: failedSnapshot,
            },
          });
          try {
            await uploadDocumentStream(canonicalPath(scope, file.name), file, {
              ...options,
              onStage: onStage(scope),
            });
          } catch (err) {
            if (isAbortError(err)) break;
            failedSnapshot = [...failedSnapshot, file.name];
          }
        }
        await silentRefresh(scope);
      } finally {
        patch(scope, { isUploading: false, uploadProgress: null, operationStage: null });
      }
    },

    uploadCol: async (scope, file, options) => {
      patch(scope, { isUploading: true, error: null, uploadProgress: null });
      try {
        const result = await uploadCollectionStream(scope, file, {
          ...options,
          onProgress: (uploadProgress) => patch(scope, { uploadProgress }),
        });
        await silentRefresh(scope);
        return result;
      } catch (err) {
        if (!isAbortError(err)) {
          patch(scope, { error: err instanceof Error ? err.message : "Collection upload failed" });
        }
        throw err;
      } finally {
        patch(scope, { isUploading: false, uploadProgress: null });
      }
    },

    remove: (scope, filename) =>
      withMutating(scope, filename, "Delete failed", () =>
        deleteDocument(canonicalPath(scope, filename)),
      ),

    rechunk: (scope, filename, spec) =>
      withMutating(scope, filename, "Rechunk failed", () =>
        rechunkDocumentStream(canonicalPath(scope, filename), spec, { onStage: onStage(scope) }),
      ),

    reconvert: (scope, filename, options) =>
      withMutating(scope, filename, "Reconvert failed", () =>
        reconvertDocumentStream(canonicalPath(scope, filename), {
          ...options,
          onStage: onStage(scope),
        }),
      ),

    bulkRechunk: (scope, files, spec) =>
      runBulk(scope, "Bulk rechunk failed", (onProgress) =>
        bulkRechunkStream(
          files.map((f) => canonicalPath(scope, f)),
          spec,
          { onProgress },
        ),
      ),

    bulkReconvert: (scope, files, spec, llm) =>
      runBulk(scope, "Bulk reconvert failed", (onProgress) =>
        bulkReconvertStream(
          files.map((f) => canonicalPath(scope, f)),
          spec,
          llm,
          { onProgress },
        ),
      ),

    bulkDelete: (scope, files) =>
      runBulk(scope, "Bulk delete failed", (onProgress) =>
        bulkDeleteStream(
          files.map((f) => canonicalPath(scope, f)),
          { onProgress },
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
