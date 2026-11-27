import { act, renderHook } from "@testing-library/react";
import type { UIMessageChunk } from "ai";
import { beforeEach, expect, it, vi } from "vitest";

import { useHivegentChat } from "@/hooks/chat/use-hivegent-chat";
import type { ChatMessage } from "@/lib/chat/message";
import { onConversationActivity } from "@/stores/jobs-store";
import { createQueryWrapper, settle } from "@/test/render";

const { chatFetch } = vi.hoisted(() => ({
  chatFetch: vi.fn<typeof import("@/api/client").chatFetch>(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  chatFetch,
}));

vi.mock("@/stores/jobs-store", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/stores/jobs-store")>()),
  onConversationActivity: vi.fn<typeof onConversationActivity>(() => () => {}),
}));

beforeEach(() => {
  chatFetch.mockReset();
});

const conversationId = "00000000-0000-4000-8000-0000000000c1";
const sse = (...chunks: (UIMessageChunk | "[DONE]")[]) =>
  chunks.map((chunk) => `data: ${chunk === "[DONE]" ? chunk : JSON.stringify(chunk)}\n\n`).join("");
const eventHeaders = { "Content-Type": "text/event-stream" };

it.each<[UIMessageChunk, boolean]>([
  [{ type: "data-compaction", data: { content: "gist" } }, true],
  [{ type: "data-compaction", data: { content: 42 } }, false],
  [{ type: "message-metadata", messageMetadata: { chatError: "failed" } }, true],
  [{ type: "message-metadata", messageMetadata: { chatError: 42 } }, false],
])("validates streamed %j", async (chunk, valid) => {
  // A stream without the stored turn is followed again, which finds no turn running.
  chatFetch
    .mockResolvedValueOnce(new Response(sse(chunk, "[DONE]"), { headers: eventHeaders }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  const { result } = renderHook(() => useHivegentChat(conversationId, []), {
    wrapper: createQueryWrapper(),
  });

  await act(() => result.current.sendMessage({ text: "Hi" }));

  expect(result.current.status).toBe(valid ? "ready" : "error");
  expect(result.current.error?.message ?? "").toMatch(valid ? /^$/ : /Type validation failed/);
});

it("follows a turn the feed announces while the chat streams once it settled", async () => {
  const user: ChatMessage = { id: "u1", role: "user", parts: [{ type: "text", text: "Hi" }] };
  const turn = (text: string) =>
    sse(
      { type: "start", messageId: "a1" },
      {
        type: "data-turn",
        data: [user, { id: "a1", role: "assistant", parts: [{ type: "text", text }] }],
        transient: true,
      },
      { type: "finish" },
      "[DONE]",
    );
  const settled = Promise.withResolvers<void>();
  const first = new ReadableStream<Uint8Array>({
    async pull(controller) {
      await settled.promise;
      controller.enqueue(new TextEncoder().encode(turn("Waiting")));
      controller.close();
    },
  });
  chatFetch
    .mockResolvedValueOnce(new Response(first, { headers: eventHeaders }))
    .mockResolvedValueOnce(new Response(turn("Reported"), { headers: eventHeaders }));
  const { result } = renderHook(() => useHivegentChat(conversationId, []), {
    wrapper: createQueryWrapper(),
  });

  act(() => void result.current.sendMessage(user));
  await settle();

  expect(chatFetch).toHaveBeenCalledOnce();

  act(() => vi.mocked(onConversationActivity).mock.lastCall?.[2]());
  settled.resolve();
  await settle();

  expect(result.current.messages.at(-1)?.parts).toEqual([{ type: "text", text: "Reported" }]);
  expect(chatFetch.mock.lastCall?.[0]).toMatch(/\/stream$/);
});

it("rebuilds the reply from the replay of a turn whose stream dropped", async () => {
  const replay: UIMessageChunk[] = [
    { type: "start", messageId: "a1" },
    { type: "start-step" },
    { type: "tool-input-start", toolCallId: "c1", toolName: "list_documents" },
    { type: "tool-input-delta", toolCallId: "c1", inputTextDelta: '{"path":' },
    { type: "tool-input-start", toolCallId: "c2", toolName: "list_documents" },
  ];
  const encoded = new TextEncoder().encode(sse(...replay));
  let drop = () => {};
  const dropped = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoded);
      drop = () => controller.error(new TypeError("network error"));
    },
  });
  const following = new ReadableStream<Uint8Array>({
    start: (controller) => controller.enqueue(encoded),
  });
  chatFetch
    .mockResolvedValueOnce(new Response(dropped, { headers: eventHeaders }))
    .mockResolvedValueOnce(new Response(following, { headers: eventHeaders }));
  const { result } = renderHook(() => useHivegentChat(conversationId, []), {
    wrapper: createQueryWrapper(),
  });

  act(() => void result.current.sendMessage({ text: "Hi" }));
  await settle();
  act(() => vi.mocked(onConversationActivity).mock.lastCall?.[2]());
  act(() => drop());
  await settle();

  expect(chatFetch.mock.lastCall?.[0]).toMatch(/\/stream$/);
  expect(result.current.messages.at(-1)?.parts.map((part) => part.type)).toEqual([
    "step-start",
    "tool-list_documents",
    "tool-list_documents",
  ]);
});
