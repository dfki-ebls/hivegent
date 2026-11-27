import { act, renderHook, waitFor } from "@testing-library/react";
import type { UIMessageChunk } from "ai";
import { expect, it, vi } from "vitest";

import { useHivegentChat } from "@/hooks/chat/use-hivegent-chat";
import type { ChatMessage } from "@/lib/chat/message";
import { onConversationActivity } from "@/stores/jobs-store";
import { createQueryWrapper } from "@/test/render";

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
  chatFetch.mockResolvedValueOnce(new Response(sse(chunk, "[DONE]"), { headers: eventHeaders }));
  const { result } = renderHook(() => useHivegentChat(conversationId, []), {
    wrapper: createQueryWrapper(),
  });

  await act(() => result.current.sendMessage({ text: "Hi" }));

  expect(result.current.status).toBe(valid ? "ready" : "error");
  expect(result.current.error?.message ?? "").toMatch(valid ? /^$/ : /Type validation failed/);
});

it("follows a turn the feed announces while the chat streams once it settled", async () => {
  chatFetch.mockClear();
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
  await waitFor(() => expect(chatFetch).toHaveBeenCalledOnce());
  act(() => vi.mocked(onConversationActivity).mock.lastCall?.[2]());
  settled.resolve();

  await waitFor(() =>
    expect(result.current.messages.at(-1)?.parts).toEqual([{ type: "text", text: "Reported" }]),
  );
  expect(chatFetch.mock.lastCall?.[0]).toMatch(/\/stream$/);
});
