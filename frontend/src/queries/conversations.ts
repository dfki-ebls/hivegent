import { mutationOptions, queryOptions } from "@tanstack/react-query";

import {
  compactConversation,
  deleteConversation,
  getConversationMessages,
  generateConversationTitle,
  importConversation,
  listConversations,
  updateConversationTitle,
} from "@/lib/api";
import type { AgentRunConfig, LlmConfig } from "@/lib/types";

const all = ["conversations"] as const;

export const conversationQueries = {
  /** Every conversation query, the target of a refetch after any change. */
  all: () => all,
  /** The user's conversations, newest first. */
  list: () => queryOptions({ queryKey: [...all, "list"], queryFn: listConversations }),
  /**
   * The persisted messages of a conversation, read once per visit: the chat
   * owns them once seeded, so they never refetch, and leaving drops them, so
   * the next visit reads them afresh.
   */
  messages: (id: string) =>
    queryOptions({
      queryKey: [...all, "messages", id],
      queryFn: () => getConversationMessages(id),
      staleTime: "static",
      gcTime: 0,
    }),
};

// Every conversation mutation can change the list, so each refetches it.
const meta = { invalidates: [all] };

export const conversationMutations = {
  delete: () =>
    mutationOptions({ mutationKey: [...all, "delete"], mutationFn: deleteConversation, meta }),
  rename: () =>
    mutationOptions({
      mutationKey: [...all, "rename"],
      mutationFn: ({ id, title }: { id: string; title: string }) =>
        updateConversationTitle(id, title),
      meta,
    }),
  generateTitle: () =>
    mutationOptions({
      mutationKey: [...all, "generate-title"],
      mutationFn: ({ id, llm }: { id: string; llm: LlmConfig }) =>
        generateConversationTitle(id, llm),
      meta,
    }),
  import: () =>
    mutationOptions({ mutationKey: [...all, "import"], mutationFn: importConversation, meta }),
  compact: () =>
    mutationOptions({
      mutationKey: [...all, "compact"],
      // `retryLastMessage` is the caller's, read back once the compaction succeeds.
      mutationFn: ({ id, body }: { id: string; body: AgentRunConfig; retryLastMessage: boolean }) =>
        compactConversation(id, body),
      meta,
    }),
};
