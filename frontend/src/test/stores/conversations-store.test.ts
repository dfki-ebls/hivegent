import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/oidc", () => ({
  getOidc: vi.fn().mockResolvedValue({ isUserLoggedIn: false }),
}));

vi.mock("@/lib/api", () => ({
  listConversations: vi.fn(),
  deleteConversation: vi.fn(),
  updateConversationTitle: vi.fn(),
  generateConversationTitle: vi.fn(),
}));

import { deleteConversation, listConversations } from "@/lib/api";
import type { ConversationSummary } from "@/lib/types";
import { useConversationsStore } from "@/stores/conversations-store";

const mockConversation: ConversationSummary = {
  id: "conv1",
  title: "Test Conversation",
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
  message_count: 5,
};

describe("useConversationsStore", () => {
  beforeEach(() => {
    useConversationsStore.setState({
      conversations: [],
      isLoading: false,
      error: null,
    });
    vi.clearAllMocks();
  });

  describe("fetchConversations", () => {
    it("fetches and stores conversations", async () => {
      vi.mocked(listConversations).mockResolvedValueOnce([mockConversation]);

      await useConversationsStore.getState().fetchConversations();

      const state = useConversationsStore.getState();
      expect(state.conversations).toHaveLength(1);
      expect(state.conversations[0].id).toBe("conv1");
      expect(state.isLoading).toBe(false);
    });

    it("sets error on failure", async () => {
      vi.mocked(listConversations).mockRejectedValueOnce(new Error("Network error"));

      await useConversationsStore.getState().fetchConversations();

      const state = useConversationsStore.getState();
      expect(state.error).toBe("Network error");
      expect(state.isLoading).toBe(false);
    });
  });

  describe("deleteConversation", () => {
    it("removes conversation from store", async () => {
      useConversationsStore.setState({ conversations: [mockConversation] });
      vi.mocked(deleteConversation).mockResolvedValueOnce(undefined);

      await useConversationsStore.getState().deleteConversation("conv1");

      expect(useConversationsStore.getState().conversations).toHaveLength(0);
    });

    it("sets error and rethrows on failure", async () => {
      useConversationsStore.setState({ conversations: [mockConversation] });
      vi.mocked(deleteConversation).mockRejectedValueOnce(new Error("Delete failed"));

      await expect(useConversationsStore.getState().deleteConversation("conv1")).rejects.toThrow(
        "Delete failed",
      );

      expect(useConversationsStore.getState().error).toBe("Delete failed");
    });
  });

  describe("refreshConversation", () => {
    it("updates an existing conversation", () => {
      useConversationsStore.setState({ conversations: [mockConversation] });

      useConversationsStore.getState().refreshConversation("conv1", { title: "Updated Title" });

      const conv = useConversationsStore.getState().conversations[0];
      expect(conv.title).toBe("Updated Title");
    });

    it("adds a new conversation if it does not exist", () => {
      useConversationsStore.setState({ conversations: [] });

      useConversationsStore.getState().refreshConversation("new1", {
        id: "new1",
        title: "New Conversation",
      });

      expect(useConversationsStore.getState().conversations).toHaveLength(1);
      expect(useConversationsStore.getState().conversations[0].title).toBe("New Conversation");
    });
  });
});
