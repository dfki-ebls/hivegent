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
  isLoading: boolean;
  uploadProgress: UploadProgress | null;
  bulkProgress: UploadProgress | null;
  error: string | null;
  fetchDocuments: () => Promise<void>;
  fetchDirectoryTree: () => Promise<void>;
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

export const useUserDocumentsStore = create<UserDocumentsStore>((set, get) => ({
  documents: [],
  directoryTree: null,
  isLoading: false,
  uploadProgress: null,
  bulkProgress: null,
  error: null,

  fetchDocuments: async () => {
    set({ isLoading: true, error: null });
    try {
      const documents = await listDocuments();
      set({ documents, isLoading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to fetch documents",
        isLoading: false,
      });
    }
  },

  fetchDirectoryTree: async () => {
    try {
      const directoryTree = await getDirectories();
      set({ directoryTree });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to fetch directory tree",
      });
    }
  },

  upload: async (file: File, options?: UploadDocumentOptions) => {
    set({ isLoading: true, error: null });
    try {
      await uploadDocument(file.name, file, options);
      await get().fetchDocuments();
      await get().fetchDirectoryTree();
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Upload failed",
        isLoading: false,
      });
    }
  },

  uploadMultiple: async (files: File[], options?: UploadDocumentOptions) => {
    set({ isLoading: true, error: null, uploadProgress: null });
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
      await Promise.all([get().fetchDocuments(), get().fetchDirectoryTree()]);
    } finally {
      set({ isLoading: false, uploadProgress: null });
    }
  },

  uploadCol: async (file: File, options?: UploadCollectionOptions & { signal?: AbortSignal }) => {
    set({ isLoading: true, error: null, uploadProgress: null });
    try {
      const result = await uploadCollectionStream(file, {
        ...options,
        onProgress: (progress) => set({ uploadProgress: progress }),
      });
      await Promise.all([get().fetchDocuments(), get().fetchDirectoryTree()]);
      set({ isLoading: false, uploadProgress: null });
      return result;
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Collection upload failed",
        isLoading: false,
        uploadProgress: null,
      });
      throw err;
    }
  },

  remove: async (filename: string) => {
    set({ isLoading: true, error: null });
    try {
      await deleteDocument(filename);
      await get().fetchDocuments();
      await get().fetchDirectoryTree();
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Delete failed",
        isLoading: false,
      });
    }
  },

  rechunk: async (filename: string, spec?: PipelineSpec) => {
    set({ isLoading: true, error: null });
    try {
      await rechunkDocument(filename, spec);
      await get().fetchDocuments();
      await get().fetchDirectoryTree();
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Rechunk failed",
        isLoading: false,
      });
    }
  },

  reconvert: async (filename: string, options?: ReconvertDocumentOptions) => {
    set({ isLoading: true, error: null });
    try {
      await reconvertDocument(filename, options);
      await get().fetchDocuments();
      await get().fetchDirectoryTree();
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Reconvert failed",
        isLoading: false,
      });
    }
  },

  bulkRechunk: async (files: string[], spec?: PipelineSpec) => {
    set({ bulkProgress: null, error: null });
    try {
      await bulkRechunkStream(files, spec, {
        onProgress: (progress) => set({ bulkProgress: progress }),
      });
      await Promise.all([get().fetchDocuments(), get().fetchDirectoryTree()]);
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
      await Promise.all([get().fetchDocuments(), get().fetchDirectoryTree()]);
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
      await Promise.all([get().fetchDocuments(), get().fetchDirectoryTree()]);
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Bulk delete failed",
      });
    } finally {
      set({ bulkProgress: null });
    }
  },

  move: async (filepath: string, destination: string) => {
    set({ isLoading: true, error: null });
    try {
      await moveDocument(filepath, destination);
      await get().fetchDocuments();
      await get().fetchDirectoryTree();
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Move failed",
        isLoading: false,
      });
    }
  },

  createDir: async (path: string) => {
    set({ isLoading: true, error: null });
    try {
      await createDirectory(path);
      await get().fetchDirectoryTree();
      set({ isLoading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to create directory",
        isLoading: false,
      });
    }
  },

  deleteDir: async (path: string) => {
    set({ isLoading: true, error: null });
    try {
      await deleteDirectory(path);
      await get().fetchDocuments();
      await get().fetchDirectoryTree();
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to delete directory",
        isLoading: false,
      });
    }
  },

  clearError: () => set({ error: null }),
}));
