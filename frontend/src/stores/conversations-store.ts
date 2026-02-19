import { create } from "zustand";
import {
  deleteConversation as apiDeleteConversation,
  generateConversationTitle,
  listConversations,
  updateConversationTitle,
} from "../lib/api";
import type { ConversationSummary, LlmConfig } from "../lib/types";

interface ConversationsState {
  conversations: ConversationSummary[];
  isLoading: boolean;
  error: string | null;
  fetchConversations: () => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  updateTitle: (id: string, title: string) => Promise<void>;
  generateTitle: (id: string, llm: LlmConfig) => Promise<string>;
  refreshConversation: (
    id: string,
    summary: Partial<ConversationSummary>,
  ) => void;
}

export const useConversationsStore = create<ConversationsState>((set) => ({
  conversations: [],
  isLoading: false,
  error: null,

  fetchConversations: async () => {
    set({ isLoading: true, error: null });
    try {
      const conversations = await listConversations();
      set({ conversations, isLoading: false });
    } catch (e) {
      set({
        error: e instanceof Error ? e.message : "Failed to fetch conversations",
        isLoading: false,
      });
    }
  },

  deleteConversation: async (id: string) => {
    try {
      await apiDeleteConversation(id);
      set((state) => ({
        conversations: state.conversations.filter((c) => c.id !== id),
      }));
    } catch (e) {
      set({
        error: e instanceof Error ? e.message : "Failed to delete conversation",
      });
      throw e;
    }
  },

  updateTitle: async (id: string, title: string) => {
    try {
      const updated = await updateConversationTitle(id, title);
      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === id ? { ...c, title: updated.title } : c,
        ),
      }));
    } catch (e) {
      set({
        error: e instanceof Error ? e.message : "Failed to update title",
      });
      throw e;
    }
  },

  generateTitle: async (id: string, llm: LlmConfig) => {
    try {
      const result = await generateConversationTitle(id, llm);
      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === id ? { ...c, title: result.title } : c,
        ),
      }));
      return result.title;
    } catch (e) {
      set({
        error: e instanceof Error ? e.message : "Failed to generate title",
      });
      throw e;
    }
  },

  refreshConversation: (id: string, summary: Partial<ConversationSummary>) => {
    set((state) => {
      const existing = state.conversations.find((c) => c.id === id);
      if (existing) {
        return {
          conversations: state.conversations.map((c) =>
            c.id === id ? { ...c, ...summary } : c,
          ),
        };
      }
      // Add new conversation at the beginning if it doesn't exist
      if (summary.id && summary.title !== undefined) {
        return {
          conversations: [
            {
              id: summary.id,
              title: summary.title,
              created_at: summary.created_at || new Date().toISOString(),
              updated_at: summary.updated_at || new Date().toISOString(),
              message_count: summary.message_count || 0,
            },
            ...state.conversations,
          ],
        };
      }
      return state;
    });
  },
}));
