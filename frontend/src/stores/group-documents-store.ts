import { create } from "zustand";

import type { UploadCollectionOptions, UploadDocumentOptions } from "../lib/api";
import {
  createGroupDirectory,
  deleteGroupDirectory,
  deleteGroupDocument,
  getGroupDirectories,
  uploadGroupCollectionStream,
  uploadGroupDocument,
} from "../lib/api";
import type { CollectionUploadResponse, DirectoryTreeResponse, UploadProgress } from "../lib/types";

interface GroupDocumentsStore {
  selectedGroupId: string | null;
  directoryTree: DirectoryTreeResponse | null;
  isLoading: boolean;
  uploadProgress: UploadProgress | null;
  error: string | null;

  selectGroup: (groupId: string | null) => void;
  fetchDirectoryTree: () => Promise<void>;
  upload: (file: File, options?: UploadDocumentOptions) => Promise<void>;
  uploadMultiple: (files: File[], options?: UploadDocumentOptions) => Promise<void>;
  uploadCol: (
    file: File,
    options?: UploadCollectionOptions & { signal?: AbortSignal },
  ) => Promise<CollectionUploadResponse>;
  remove: (filename: string) => Promise<void>;
  createDir: (path: string) => Promise<void>;
  deleteDir: (path: string) => Promise<void>;
  clearError: () => void;
}

export const useGroupDocumentsStore = create<GroupDocumentsStore>((set, get) => ({
  selectedGroupId: null,
  directoryTree: null,
  isLoading: false,
  uploadProgress: null,
  error: null,

  selectGroup: (groupId: string | null) => {
    set({
      selectedGroupId: groupId,
      directoryTree: null,
      error: null,
    });
  },

  fetchDirectoryTree: async () => {
    const groupId = get().selectedGroupId;
    if (!groupId) return;
    try {
      const directoryTree = await getGroupDirectories(groupId);
      set({ directoryTree });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to fetch directory tree",
      });
    }
  },

  upload: async (file: File, options?: UploadDocumentOptions) => {
    const groupId = get().selectedGroupId;
    if (!groupId) return;
    set({ isLoading: true, error: null });
    try {
      await uploadGroupDocument(groupId, file.name, file, options);
      await get().fetchDirectoryTree();
      set({ isLoading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Upload failed",
        isLoading: false,
      });
    }
  },

  uploadMultiple: async (files: File[], options?: UploadDocumentOptions) => {
    const groupId = get().selectedGroupId;
    if (!groupId) return;
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
          await uploadGroupDocument(groupId, file.name, file, options);
        } catch {
          failedSnapshot = [...failedSnapshot, file.name];
        }
      }
      await get().fetchDirectoryTree();
    } finally {
      set({ isLoading: false, uploadProgress: null });
    }
  },

  uploadCol: async (file: File, options?: UploadCollectionOptions & { signal?: AbortSignal }) => {
    const groupId = get().selectedGroupId;
    if (!groupId) throw new Error("No group selected");
    set({ isLoading: true, error: null, uploadProgress: null });
    try {
      const result = await uploadGroupCollectionStream(groupId, file, {
        ...options,
        onProgress: (progress) => set({ uploadProgress: progress }),
      });
      await get().fetchDirectoryTree();
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
    const groupId = get().selectedGroupId;
    if (!groupId) return;
    set({ isLoading: true, error: null });
    try {
      await deleteGroupDocument(groupId, filename);
      await get().fetchDirectoryTree();
      set({ isLoading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Delete failed",
        isLoading: false,
      });
    }
  },

  createDir: async (path: string) => {
    const groupId = get().selectedGroupId;
    if (!groupId) return;
    set({ isLoading: true, error: null });
    try {
      await createGroupDirectory(groupId, path);
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
    const groupId = get().selectedGroupId;
    if (!groupId) return;
    set({ isLoading: true, error: null });
    try {
      await deleteGroupDirectory(groupId, path);
      await get().fetchDirectoryTree();
      set({ isLoading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : "Failed to delete directory",
        isLoading: false,
      });
    }
  },

  clearError: () => set({ error: null }),
}));
