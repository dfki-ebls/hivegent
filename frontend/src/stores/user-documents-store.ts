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
  moveDocument,
  rechunkDocument,
  reconvertDocument,
  uploadCollectionStream,
  uploadDocument,
} from "../lib/api";
import type {
  CollectionUploadResponse,
  DirectoryTreeResponse,
  DocumentInfo,
  LlmConfig,
  PipelineSpec,
  UploadProgress,
} from "../lib/types";

interface UserDocumentsStore {
  documents: DocumentInfo[];
  directoryTree: DirectoryTreeResponse | null;
  mutatingPaths: Set<string>;
  isUploading: boolean;
  hasFetched: boolean;
  uploadProgress: UploadProgress | null;
  bulkProgress: UploadProgress | null;
  error: string | null;
  refresh: () => Promise<void>;
  upload: (file: File, options?: UploadDocumentOptions) => Promise<void>;
  uploadMultiple: (files: File[], options?: UploadDocumentOptions) => Promise<void>;
  uploadCol: (
    file: File,
    options?: UploadCollectionOptions & { signal?: AbortSignal },
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
  clearError: () => void;
}

export const useUserDocumentsStore = create<UserDocumentsStore>((set, get) => {
  async function silentRefresh() {
    const [documents, directoryTree] = await Promise.all([listDocuments(), getDirectories()]);
    set({ documents, directoryTree, hasFetched: true });
  }

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
      set({ mutatingPaths: removePath(get().mutatingPaths) });
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
    error: null,

    refresh: silentRefresh,

    upload: async (file: File, options?: UploadDocumentOptions) => {
      set({ isUploading: true, error: null });
      try {
        await uploadDocument(file.name, file, options);
        await silentRefresh();
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : "Upload failed",
        });
      } finally {
        set({ isUploading: false });
      }
    },

    uploadMultiple: async (files: File[], options?: UploadDocumentOptions) => {
      set({ isUploading: true, error: null, uploadProgress: null });
      let failedSnapshot: string[] = [];
      try {
        for (let i = 0; i < files.length; i++) {
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
            await uploadDocument(file.name, file, options);
          } catch {
            failedSnapshot = [...failedSnapshot, file.name];
          }
        }
        await silentRefresh();
      } finally {
        set({ isUploading: false, uploadProgress: null });
      }
    },

    uploadCol: async (file: File, options?: UploadCollectionOptions & { signal?: AbortSignal }) => {
      set({ isUploading: true, error: null, uploadProgress: null });
      try {
        const result = await uploadCollectionStream(file, {
          ...options,
          onProgress: (progress) => set({ uploadProgress: progress }),
        });
        await silentRefresh();
        set({ isUploading: false, uploadProgress: null });
        return result;
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : "Collection upload failed",
          isUploading: false,
          uploadProgress: null,
        });
        throw err;
      }
    },

    remove: (filename) => withMutating(filename, "Delete failed", () => deleteDocument(filename)),

    rechunk: (filename, spec) =>
      withMutating(filename, "Rechunk failed", () => rechunkDocument(filename, spec)),

    reconvert: (filename, options) =>
      withMutating(filename, "Reconvert failed", () => reconvertDocument(filename, options)),

    bulkRechunk: async (files: string[], spec?: PipelineSpec) => {
      set({ bulkProgress: null, error: null });
      try {
        await bulkRechunkStream(files, spec, {
          onProgress: (progress) => set({ bulkProgress: progress }),
        });
        await silentRefresh();
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : "Bulk rechunk failed",
        });
      } finally {
        set({ bulkProgress: null });
      }
    },

    bulkReconvert: async (files: string[], spec?: PipelineSpec, llm?: LlmConfig) => {
      set({ bulkProgress: null, error: null });
      try {
        await bulkReconvertStream(files, spec, llm, {
          onProgress: (progress) => set({ bulkProgress: progress }),
        });
        await silentRefresh();
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : "Bulk reconvert failed",
        });
      } finally {
        set({ bulkProgress: null });
      }
    },

    bulkDelete: async (files: string[]) => {
      set({ bulkProgress: null, error: null });
      try {
        await bulkDeleteStream(files, {
          onProgress: (progress) => set({ bulkProgress: progress }),
        });
        await silentRefresh();
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : "Bulk delete failed",
        });
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

    clearError: () => set({ error: null }),
  };
});
