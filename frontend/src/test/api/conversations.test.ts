import { act, renderHook, waitFor } from "@testing-library/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";

import {
  type ConversationSummary,
  conversationMutations,
  conversationQueries,
  getConversationMessages,
  syncConversationQueries,
} from "@/api/conversations";
import { type FeedHandlers, subscribeJobs } from "@/api/jobs";
import { useJobsStore } from "@/stores/jobs-store";
import { createQueryWrapper, createTestQueryClient } from "@/test/render";

vi.mock("@/api/jobs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/jobs")>()),
  subscribeJobs: vi.fn<typeof subscribeJobs>(),
}));

const { requestJson, requestVoid } = vi.hoisted(() => ({
  requestJson: vi.fn<(url: string) => Promise<unknown>>(),
  requestVoid: vi.fn<(url: string) => Promise<void>>(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  requestJson,
  requestVoid,
}));

const A = "00000000-0000-4000-8000-00000000000a";
const B = "00000000-0000-4000-8000-00000000000b";
const C1 = "00000000-0000-4000-8000-0000000000c1";

const summary = (id: string): ConversationSummary => ({
  id,
  title: id,
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
});

it("refetches the list after a delete and once for a burst of feed events", async () => {
  requestJson
    .mockResolvedValueOnce({ conversations: [summary(A), summary(B)] })
    .mockResolvedValueOnce({ conversations: [summary(B)] })
    .mockResolvedValueOnce({ conversations: [] });
  requestVoid.mockResolvedValue();
  const feed = Promise.withResolvers<FeedHandlers>();
  vi.mocked(subscribeJobs).mockImplementation(async (handlers) => {
    feed.resolve(handlers);
    await new Promise<never>(() => {});
  });
  const client = createTestQueryClient();
  syncConversationQueries(client);
  useJobsStore.getState().start();

  const { result } = renderHook(
    () => ({
      list: useQuery(conversationQueries.list()),
      remove: useMutation(conversationMutations.delete()),
    }),
    { wrapper: createQueryWrapper(client) },
  );

  await waitFor(() => expect(result.current.list.data).toHaveLength(2));
  await act(() => result.current.remove.mutateAsync(A));

  expect(requestVoid).toHaveBeenCalledWith(
    expect.stringContaining(`/api/v1/conversations/${A}`),
    "delete",
    { method: "DELETE" },
  );
  await waitFor(() => expect(result.current.list.data).toEqual([summary(B)]));

  const { onConversationsChanged } = await feed.promise;
  act(() => [1, 2, 3].forEach(() => onConversationsChanged()));

  await waitFor(() => expect(result.current.list.data).toEqual([]));
  expect(requestJson).toHaveBeenCalledTimes(3);
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

  const messages = await getConversationMessages(C1);

  expect(messages.map((message) => message.metadata)).toEqual([undefined, { chatError: "failed" }]);
});
