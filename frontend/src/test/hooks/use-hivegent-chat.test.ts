import { act, renderHook } from "@testing-library/react";
import type { UIMessageChunk } from "ai";
import { expect, it, vi } from "vitest";

import { useHivegentChat } from "@/hooks/chat/use-hivegent-chat";
import { createQueryWrapper } from "@/test/render";

const { chatFetch } = vi.hoisted(() => ({
  chatFetch: vi.fn<typeof import("@/api/client").chatFetch>(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  chatFetch,
}));

const subagent = { tool_call_id: "t1", transcript: { transcript: "subagent", steps: [] } };

it.each<[UIMessageChunk, boolean]>([
  [{ type: "data-compaction", data: { content: "gist" } }, true],
  [{ type: "data-compaction", data: { content: 42 } }, false],
  [{ type: "data-subagent", transient: true, data: subagent }, true],
  [{ type: "data-subagent", transient: true, data: { tool_call_id: 42 } }, false],
  [{ type: "message-metadata", messageMetadata: { chatError: "failed" } }, true],
  [{ type: "message-metadata", messageMetadata: { chatError: 42 } }, false],
])("validates streamed %j", async (chunk, valid) => {
  chatFetch.mockResolvedValueOnce(
    new Response(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`, {
      headers: { "Content-Type": "text/event-stream" },
    }),
  );
  const { result } = renderHook(() => useHivegentChat("c1", []), {
    wrapper: createQueryWrapper(),
  });

  await act(() => result.current.chat.sendMessage({ text: "Hi" }));

  expect(result.current.chat.status).toBe(valid ? "ready" : "error");
  expect(result.current.chat.error?.message ?? "").toMatch(
    valid ? /^$/ : /Type validation failed/,
  );
});
