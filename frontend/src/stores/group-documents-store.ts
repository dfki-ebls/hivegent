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
import { isAbortError } from "../lib/utils";

type Signalled<T> = T & { signal?: AbortSignal };

interface GroupDocumentsStore {
  selectedGroupId: string | null;
  directoryTree: DirectoryTreeResponse | null;
  isLoading: boolean;
  uploadProgress: UploadProgress | null;
  error: string | null;

  selectGroup: (groupId: string | null) => void;
  fetchDirectoryTree: () => Promise<void>;
  upload: (file: File, options?: UploadDocumentOptions) => Promise<void>;
  uploadMultiple: (files: File[], options?: Signalled<UploadDocumentOptions>) => Promise<void>;
  uploadCol: (
    file: File,
    options?: Signalled<UploadCollectionOptions>,
  ) => Promise<CollectionUploadResponse>;
  remove: (filename: string) => Promise<void>;
  createDir: (path: string) => Promise<void>;
  deleteDir: (path: string) => Promise<void>;
  clearError: () => void;
}

export const useGroupDocumentsStore = create<GroupDocumentsStore>((set, get) => {
  /** Run a group mutation with shared loading/error handling and tree refresh. */
  async function withMutating(
    errorMsg: string,
    operation: (groupId: string) => Promise<unknown>,
  ): Promise<void> {
    const groupId = get().selectedGroupId;
    if (!groupId) return;
    set({ isLoading: true, error: null });
    try {
      await operation(groupId);
      await get().fetchDirectoryTree();
    } catch (err) {
      set({ error: err instanceof Error ? err.message : errorMsg });
    } finally {
      set({ isLoading: false });
    }
  }

  return {
    selectedGroupId: null,
    directoryTree: null,
    isLoading: false,
    uploadProgress: null,
    error: null,

    selectGroup: (groupId) => {
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

    upload: (file, options) =>
      withMutating("Upload failed", (groupId) =>
        uploadGroupDocument(groupId, file.name, file, options),
      ),

    uploadMultiple: async (files, options) => {
      const groupId = get().selectedGroupId;
      if (!groupId) return;
      const signal = options?.signal;
      set({ isLoading: true, error: null, uploadProgress: null });
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
            await uploadGroupDocument(groupId, file.name, file, options);
          } catch (err) {
            if (isAbortError(err)) break;
            failedSnapshot = [...failedSnapshot, file.name];
          }
        }
        await get().fetchDirectoryTree();
      } finally {
        set({ isLoading: false, uploadProgress: null });
      }
    },

    uploadCol: async (file, options) => {
      const groupId = get().selectedGroupId;
      if (!groupId) throw new Error("No group selected");
      set({ isLoading: true, error: null, uploadProgress: null });
      try {
        const result = await uploadGroupCollectionStream(groupId, file, {
          ...options,
          onProgress: (progress) => set({ uploadProgress: progress }),
        });
        await get().fetchDirectoryTree();
        return result;
      } catch (err) {
        if (!isAbortError(err)) {
          set({ error: err instanceof Error ? err.message : "Collection upload failed" });
        }
        throw err;
      } finally {
        set({ isLoading: false, uploadProgress: null });
      }
    },

    remove: (filename) =>
      withMutating("Delete failed", (groupId) => deleteGroupDocument(groupId, filename)),

    createDir: (path) =>
      withMutating("Failed to create directory", (groupId) => createGroupDirectory(groupId, path)),

    deleteDir: (path) =>
      withMutating("Failed to delete directory", (groupId) => deleteGroupDirectory(groupId, path)),

    clearError: () => set({ error: null }),
  };
});
