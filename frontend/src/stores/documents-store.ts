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
  createDirectory,
  deleteDirectory,
  deleteDocument,
  getDirectories,
  listDocuments,
  moveDirectory,
  moveDocument,
  rechunkDocumentStream,
  reconvertDocumentStream,
  scopePrefix,
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
import { isAbortError } from "../lib/utils";

type Signalled<T> = T & { signal?: AbortSignal };

/** Per-scope document-management state. A scope is "" (personal) or a group id. */
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

export const EMPTY_SCOPE: ScopeState = {
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
  const scopeState = (scope: string): ScopeState => get().byScope[scope] ?? EMPTY_SCOPE;

  const patch = (
    scope: string,
    update: Partial<ScopeState> | ((s: ScopeState) => Partial<ScopeState>),
  ) =>
    set((store) => {
      const current = store.byScope[scope] ?? EMPTY_SCOPE;
      const delta = typeof update === "function" ? update(current) : update;
      return { byScope: { ...store.byScope, [scope]: { ...current, ...delta } } };
    });

  const onStage = (scope: string) => (stage: OperationStage) => {
    const current = scopeState(scope).operationStage;
    if (current?.stage === stage.stage && current?.detail === stage.detail) return;
    patch(scope, { operationStage: stage });
  };

  const silentRefresh = async (scope: string) => {
    const [documents, directoryTree] = await Promise.all([
      listDocuments(scope),
      getDirectories(scope),
    ]);
    patch(scope, { documents, directoryTree, hasFetched: true });
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
        await uploadDocumentStream(file.name, file, { ...options, scope, onStage: onStage(scope) });
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
            await uploadDocumentStream(file.name, file, {
              ...options,
              scope,
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
        const result = await uploadCollectionStream(file, {
          ...options,
          scope,
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
        deleteDocument(`${scopePrefix(scope)}${filename}`),
      ),

    rechunk: (scope, filename, spec) =>
      withMutating(scope, filename, "Rechunk failed", () =>
        rechunkDocumentStream(`${scopePrefix(scope)}${filename}`, spec, { onStage: onStage(scope) }),
      ),

    reconvert: (scope, filename, options) =>
      withMutating(scope, filename, "Reconvert failed", () =>
        reconvertDocumentStream(`${scopePrefix(scope)}${filename}`, {
          ...options,
          onStage: onStage(scope),
        }),
      ),

    bulkRechunk: (scope, files, spec) =>
      runBulk(scope, "Bulk rechunk failed", (onProgress) =>
        bulkRechunkStream(
          files.map((f) => `${scopePrefix(scope)}${f}`),
          spec,
          { onProgress },
        ),
      ),

    bulkReconvert: (scope, files, spec, llm) =>
      runBulk(scope, "Bulk reconvert failed", (onProgress) =>
        bulkReconvertStream(
          files.map((f) => `${scopePrefix(scope)}${f}`),
          spec,
          llm,
          { onProgress },
        ),
      ),

    bulkDelete: (scope, files) =>
      runBulk(scope, "Bulk delete failed", (onProgress) =>
        bulkDeleteStream(
          files.map((f) => `${scopePrefix(scope)}${f}`),
          { onProgress },
        ),
      ),

    move: (scope, filepath, destination) =>
      withMutating(scope, filepath, "Move failed", () =>
        moveDocument(`${scopePrefix(scope)}${filepath}`, `${scopePrefix(scope)}${destination}`),
      ),

    createDir: (scope, path) =>
      withMutating(scope, path, "Failed to create directory", () =>
        createDirectory(`${scopePrefix(scope)}${path}`),
      ),

    deleteDir: (scope, path) =>
      withMutating(scope, path, "Failed to delete directory", () =>
        deleteDirectory(`${scopePrefix(scope)}${path}`),
      ),

    moveDir: (scope, source, destination) =>
      withMutating(scope, source, "Failed to move directory", () =>
        moveDirectory(`${scopePrefix(scope)}${source}`, `${scopePrefix(scope)}${destination}`),
      ),

    clearError: (scope) => patch(scope, { error: null }),
  };
});
