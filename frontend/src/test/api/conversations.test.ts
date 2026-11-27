import { useMutation, useQuery } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { expect, it, onTestFinished, vi } from "vitest";

import {
  type ConversationSummary,
  FEED_REFETCH_DELAY_MS,
  conversationMutations,
  conversationQueries,
  getConversationMessages,
  syncConversationQueries,
} from "@/api/conversations";
import type { subscribeJobs } from "@/api/jobs";
import { syncTaskQueries, taskQueries } from "@/api/tasks";
import { startFeed } from "@/test/feed";
import { createQueryWrapper, createTestQueryClient, settle } from "@/test/render";

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
  const client = createTestQueryClient();
  onTestFinished(syncConversationQueries(client));
  const feed = await startFeed();
  const { result } = renderHook(
    () => ({
      list: useQuery(conversationQueries.list()),
      remove: useMutation(conversationMutations.delete()),
    }),
    { wrapper: createQueryWrapper(client) },
  );

  await settle();
  expect(result.current.list.data).toHaveLength(2);

  await act(() => result.current.remove.mutateAsync(A));
  expect(requestVoid).toHaveBeenCalledWith(
    expect.stringContaining(`/api/v1/conversations/${A}`),
    "delete",
    { method: "DELETE" },
  );
  expect(result.current.list.data).toEqual([summary(B)]);

  act(() => [1, 2, 3].forEach(() => feed.onConversationsChanged()));
  await settle(FEED_REFETCH_DELAY_MS);

  expect(result.current.list.data).toEqual([]);
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

it("invalidates every transcript of the conversation for a task event", async () => {
  const client = createTestQueryClient();
  const transcript = taskQueries.messages(C1, "finished").queryKey;
  client.setQueryData(transcript, []);
  onTestFinished(syncTaskQueries(client, C1));
  const feed = await startFeed();

  feed.onConversationActivity({
    type: "conversation-activity",
    conversation_id: C1,
    kind: "tasks",
  });
  await vi.advanceTimersByTimeAsync(FEED_REFETCH_DELAY_MS);

  expect(client.getQueryState(transcript)?.isInvalidated).toBe(true);
});
