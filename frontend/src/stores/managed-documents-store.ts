import { create } from 'zustand';
import type { ChunkingPipeline, CollectionUploadResponse, DirectoryTreeResponse, DocumentInfo } from '../lib/types';
import type { ReconvertDocumentOptions, UploadCollectionOptions, UploadDocumentOptions } from '../lib/api';
import {
  createDirectory,
  deleteDirectory,
  deleteDocument,
  getDirectoryTree,
  listDocuments,
  moveDocument,
  rechunkDocument,
  reconvertDocument,
  uploadCollection,
  uploadDocument,
} from '../lib/api';

interface ManagedDocumentsStore {
  documents: DocumentInfo[];
  directoryTree: DirectoryTreeResponse | null;
  isLoading: boolean;
  error: string | null;
  fetchDocuments: () => Promise<void>;
  fetchDirectoryTree: () => Promise<void>;
  upload: (file: File, options?: UploadDocumentOptions) => Promise<void>;
  uploadCol: (file: File, options?: UploadCollectionOptions) => Promise<CollectionUploadResponse>;
  remove: (filename: string) => Promise<void>;
  rechunk: (filename: string, chunkingPipeline?: ChunkingPipeline) => Promise<void>;
  reconvert: (filename: string, options?: ReconvertDocumentOptions) => Promise<void>;
  move: (filepath: string, destination: string) => Promise<void>;
  createDir: (path: string) => Promise<void>;
  deleteDir: (path: string) => Promise<void>;
  clearError: () => void;
}

export const useManagedDocumentsStore = create<ManagedDocumentsStore>(
  (set, get) => ({
    documents: [],
    directoryTree: null,
    isLoading: false,
    error: null,

    fetchDocuments: async () => {
      set({ isLoading: true, error: null });
      try {
        const documents = await listDocuments();
        set({ documents, isLoading: false });
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : 'Failed to fetch documents',
          isLoading: false,
        });
      }
    },

    fetchDirectoryTree: async () => {
      try {
        const directoryTree = await getDirectoryTree();
        set({ directoryTree });
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : 'Failed to fetch directory tree',
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
          error: err instanceof Error ? err.message : 'Upload failed',
          isLoading: false,
        });
      }
    },

    uploadCol: async (file: File, options?: UploadCollectionOptions) => {
      set({ isLoading: true, error: null });
      try {
        const result = await uploadCollection(file, options);
        await get().fetchDocuments();
        await get().fetchDirectoryTree();
        set({ isLoading: false });
        return result;
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : 'Collection upload failed',
          isLoading: false,
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
          error: err instanceof Error ? err.message : 'Delete failed',
          isLoading: false,
        });
      }
    },

    rechunk: async (filename: string, chunkingPipeline?: ChunkingPipeline) => {
      set({ isLoading: true, error: null });
      try {
        await rechunkDocument(filename, chunkingPipeline);
        await get().fetchDocuments();
        await get().fetchDirectoryTree();
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : 'Rechunk failed',
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
          error: err instanceof Error ? err.message : 'Reconvert failed',
          isLoading: false,
        });
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
          error: err instanceof Error ? err.message : 'Move failed',
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
          error: err instanceof Error ? err.message : 'Failed to create directory',
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
          error: err instanceof Error ? err.message : 'Failed to delete directory',
          isLoading: false,
        });
      }
    },

    clearError: () => set({ error: null }),
  })
);
