import { act, renderHook, waitFor } from "@testing-library/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";

import {
  type ConversationSummary,
  conversationMutations,
  conversationQueries,
  getConversationMessages,
} from "@/api/conversations";
import { createQueryWrapper } from "@/test/render";

const { requestJson, requestVoid } = vi.hoisted(() => ({
  requestJson: vi.fn<(url: string) => Promise<unknown>>(),
  requestVoid: vi.fn<(url: string) => Promise<void>>(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  requestJson,
  requestVoid,
}));

const summary = (id: string): ConversationSummary => ({
  id,
  title: id,
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
});

it("refetches the list a mutation invalidates before it settles", async () => {
  requestJson
    .mockResolvedValueOnce({ conversations: [summary("a"), summary("b")] })
    .mockResolvedValueOnce({ conversations: [summary("b")] });
  requestVoid.mockResolvedValue();

  const { result } = renderHook(
    () => ({
      list: useQuery(conversationQueries.list()),
      remove: useMutation(conversationMutations.delete()),
    }),
    { wrapper: createQueryWrapper() },
  );

  await waitFor(() => expect(result.current.list.data).toHaveLength(2));
  await act(() => result.current.remove.mutateAsync("a"));

  expect(requestVoid).toHaveBeenCalledWith(
    expect.stringContaining("/api/conversations/a"),
    "delete",
    { method: "DELETE" },
  );
  expect(requestJson).toHaveBeenCalledTimes(2);
  await waitFor(() => expect(result.current.list.data).toEqual([summary("b")]));
});

it("validates the stored messages a chat starts from", async () => {
  requestJson.mockResolvedValueOnce([
    { id: "u1", role: "user", metadata: null, parts: [{ type: "text", text: "Hi" }] },
    {
      id: "a1",
      role: "assistant",
      metadata: { chatError: "failed", branch: { branchCount: 1 } },
      parts: [{ type: "data-compaction", data: { content: "gist" } }],
    },
  ]);

  const messages = await getConversationMessages("c1");

  expect(messages.map((message) => message.metadata)).toEqual([undefined, { chatError: "failed" }]);
});
