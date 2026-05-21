import { create } from "zustand";
import type {
  ReconvertDocumentOptions,
  StreamingOperationOptions,
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
  moveDirectory,
  getDirectories,
  listDocuments,
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
import { isAbortError } from "../lib/utils";

type Signalled<T> = T & { signal?: AbortSignal };

interface UserDocumentsStore {
  documents: DocumentInfo[];
  directoryTree: DirectoryTreeResponse | null;
  mutatingPaths: Set<string>;
  isUploading: boolean;
  hasFetched: boolean;
  uploadProgress: UploadProgress | null;
  bulkProgress: UploadProgress | null;
  operationStage: OperationStage | null;
  error: string | null;
  refresh: () => Promise<void>;
  upload: (file: File, options?: Signalled<UploadDocumentOptions>) => Promise<void>;
  uploadMultiple: (files: File[], options?: Signalled<UploadDocumentOptions>) => Promise<void>;
  uploadCol: (
    file: File,
    options?: Signalled<UploadCollectionOptions>,
  ) => Promise<CollectionUploadResponse>;
  remove: (filename: string) => Promise<void>;
  rechunk: (filename: string, spec?: PipelineSpec) => Promise<void>;
  reconvert: (filename: string, options?: ReconvertDocumentOptions) => Promise<void>;
  bulkRechunk: (files: string[], spec?: PipelineSpec) => Promise<void>;
  bulkReconvert: (files: string[], spec?: PipelineSpec, llm?: LlmConfig) => Promise<void>;
  bulkDelete: (files: string[]) => Promise<void>;
  move: (filepath: string, destination: string) => Promise<void>;
  createDir: (path: string) => Promise<void>;
  deleteDir: (path: string) => Promise<void>;
  moveDir: (source: string, destination: string) => Promise<void>;
  clearError: () => void;
}

export const useUserDocumentsStore = create<UserDocumentsStore>((set, get) => {
  async function silentRefresh() {
    const [documents, directoryTree] = await Promise.all([listDocuments(), getDirectories()]);
    set({ documents, directoryTree, hasFetched: true });
  }

  const onStage = (stage: OperationStage) => {
    const current = get().operationStage;
    if (current?.stage === stage.stage && current?.detail === stage.detail) return;
    set({ operationStage: stage });
  };
  const stageOpts: StreamingOperationOptions = { onStage };

  async function withMutating(
    path: string,
    errorMsg: string,
    operation: () => Promise<unknown>,
  ): Promise<void> {
    const addPath = (prev: Set<string>) => {
      const next = new Set(prev);
      next.add(path);
      return next;
    };
    const removePath = (prev: Set<string>) => {
      const next = new Set(prev);
      next.delete(path);
      return next;
    };

    set({ mutatingPaths: addPath(get().mutatingPaths), error: null });
    try {
      await operation();
      await silentRefresh();
    } catch (err) {
      set({ error: err instanceof Error ? err.message : errorMsg });
    } finally {
      set({ mutatingPaths: removePath(get().mutatingPaths), operationStage: null });
    }
  }

  return {
    documents: [],
    directoryTree: null,
    mutatingPaths: new Set<string>(),
    isUploading: false,
    hasFetched: false,
    uploadProgress: null,
    bulkProgress: null,
    operationStage: null,
    error: null,

    refresh: silentRefresh,

    upload: async (file, options) => {
      set({ isUploading: true, operationStage: null, error: null });
      try {
        await uploadDocumentStream(file.name, file, { ...options, ...stageOpts });
        await silentRefresh();
      } catch (err) {
        if (!isAbortError(err)) {
          set({ error: err instanceof Error ? err.message : "Upload failed" });
        }
      } finally {
        set({ isUploading: false, operationStage: null });
      }
    },

    uploadMultiple: async (files, options) => {
      const signal = options?.signal;
      set({ isUploading: true, error: null, uploadProgress: null });
      let failedSnapshot: string[] = [];
      try {
        for (let i = 0; i < files.length; i++) {
          if (signal?.aborted) break;
          const file = files[i];
          set({
            uploadProgress: {
              current: i,
              total: files.length,
              currentFile: file.name,
              failedFiles: failedSnapshot,
            },
          });
          try {
            await uploadDocumentStream(file.name, file, { ...options, ...stageOpts });
          } catch (err) {
            if (isAbortError(err)) break;
            failedSnapshot = [...failedSnapshot, file.name];
          }
        }
        await silentRefresh();
      } finally {
        set({ isUploading: false, uploadProgress: null, operationStage: null });
      }
    },

    uploadCol: async (file, options) => {
      set({ isUploading: true, error: null, uploadProgress: null });
      try {
        const result = await uploadCollectionStream(file, {
          ...options,
          onProgress: (progress) => set({ uploadProgress: progress }),
        });
        await silentRefresh();
        return result;
      } catch (err) {
        if (!isAbortError(err)) {
          set({ error: err instanceof Error ? err.message : "Collection upload failed" });
        }
        throw err;
      } finally {
        set({ isUploading: false, uploadProgress: null, operationStage: null });
      }
    },

    remove: (filename) => withMutating(filename, "Delete failed", () => deleteDocument(filename)),

    rechunk: (filename, spec) =>
      withMutating(filename, "Rechunk failed", () =>
        rechunkDocumentStream(filename, spec, stageOpts),
      ),

    reconvert: (filename, options) =>
      withMutating(filename, "Reconvert failed", () =>
        reconvertDocumentStream(filename, { ...options, ...stageOpts }),
      ),

    bulkRechunk: async (files, spec) => {
      set({ bulkProgress: null, error: null });
      try {
        await bulkRechunkStream(files, spec, {
          onProgress: (progress) => set({ bulkProgress: progress }),
        });
        await silentRefresh();
      } catch (err) {
        set({ error: err instanceof Error ? err.message : "Bulk rechunk failed" });
      } finally {
        set({ bulkProgress: null });
      }
    },

    bulkReconvert: async (files, spec, llm) => {
      set({ bulkProgress: null, error: null });
      try {
        await bulkReconvertStream(files, spec, llm, {
          onProgress: (progress) => set({ bulkProgress: progress }),
        });
        await silentRefresh();
      } catch (err) {
        set({ error: err instanceof Error ? err.message : "Bulk reconvert failed" });
      } finally {
        set({ bulkProgress: null });
      }
    },

    bulkDelete: async (files) => {
      set({ bulkProgress: null, error: null });
      try {
        await bulkDeleteStream(files, {
          onProgress: (progress) => set({ bulkProgress: progress }),
        });
        await silentRefresh();
      } catch (err) {
        set({ error: err instanceof Error ? err.message : "Bulk delete failed" });
      } finally {
        set({ bulkProgress: null });
      }
    },

    move: (filepath, destination) =>
      withMutating(filepath, "Move failed", () => moveDocument(filepath, destination)),

    createDir: (path) =>
      withMutating(path, "Failed to create directory", () => createDirectory(path)),

    deleteDir: (path) =>
      withMutating(path, "Failed to delete directory", () => deleteDirectory(path)),

    moveDir: (source, destination) =>
      withMutating(source, "Failed to move directory", () => moveDirectory(source, destination)),

    clearError: () => set({ error: null }),
  };
});
