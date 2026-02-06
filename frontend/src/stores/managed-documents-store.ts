import { create } from 'zustand';
import type { DocumentInfo } from '../lib/types';
import type { UploadDocumentOptions } from '../lib/api';
import { deleteDocument, listDocuments, uploadDocument } from '../lib/api';

interface ManagedDocumentsStore {
  documents: DocumentInfo[];
  isLoading: boolean;
  error: string | null;
  fetchDocuments: () => Promise<void>;
  upload: (file: File, options?: UploadDocumentOptions) => Promise<void>;
  remove: (filename: string) => Promise<void>;
  clearError: () => void;
}

export const useManagedDocumentsStore = create<ManagedDocumentsStore>(
  (set, get) => ({
    documents: [],
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

    upload: async (file: File, options?: UploadDocumentOptions) => {
      set({ isLoading: true, error: null });
      try {
        await uploadDocument(file.name, file, options);
        await get().fetchDocuments();
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : 'Upload failed',
          isLoading: false,
        });
      }
    },

    remove: async (filename: string) => {
      set({ isLoading: true, error: null });
      try {
        await deleteDocument(filename);
        await get().fetchDocuments();
      } catch (err) {
        set({
          error: err instanceof Error ? err.message : 'Delete failed',
          isLoading: false,
        });
      }
    },

    clearError: () => set({ error: null }),
  })
);
