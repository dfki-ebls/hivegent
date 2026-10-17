import { act, renderHook, waitFor } from "@testing-library/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";

import { deleteConversation, listConversations } from "@/lib/api";
import type { ConversationSummary } from "@/lib/types";
import { conversationMutations, conversationQueries } from "@/queries/conversations";
import { createQueryWrapper } from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  deleteConversation: vi.fn<typeof deleteConversation>().mockResolvedValue(undefined),
  listConversations: vi.fn<typeof listConversations>(),
}));

const summary = (id: string): ConversationSummary => ({
  id,
  title: id,
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
});

it("refetches the list a mutation invalidates before it settles", async () => {
  vi.mocked(listConversations)
    .mockResolvedValueOnce([summary("a"), summary("b")])
    .mockResolvedValueOnce([summary("b")]);

  const { result } = renderHook(
    () => ({
      list: useQuery(conversationQueries.list()),
      remove: useMutation(conversationMutations.delete()),
    }),
    { wrapper: createQueryWrapper() },
  );

  await waitFor(() => expect(result.current.list.data).toHaveLength(2));
  await act(() => result.current.remove.mutateAsync("a"));

  expect(deleteConversation).toHaveBeenCalledWith("a", expect.anything());
  expect(listConversations).toHaveBeenCalledTimes(2);
  await waitFor(() => expect(result.current.list.data).toEqual([summary("b")]));
});
