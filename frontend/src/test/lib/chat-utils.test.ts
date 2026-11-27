import { describe, expect, it } from "vitest";

import {
  activeChatError,
  isProcessing,
  liveOf,
  resendOf,
  settleTurn,
  shouldContinueRun,
} from "@/lib/chat/chat-utils";
import type { ChatMessage } from "@/lib/chat/message";

type Part = ChatMessage["parts"][number];

const msg = (
  role: ChatMessage["role"],
  text: string,
  metadata?: ChatMessage["metadata"],
): ChatMessage => ({ id: `${role}-${text}`, role, parts: [{ type: "text", text }], metadata });

const assistant = (...parts: Part[]): ChatMessage => ({
  id: "assistant",
  role: "assistant",
  parts,
});

const call = { type: "dynamic-tool", toolName: "search", toolCallId: "1", input: {} } as const;
const text = (state: "streaming" | "done"): Part => ({ type: "text", text: "hi", state });

it.each([
  ["submitted", [], true],
  ["streaming", [{ ...call, state: "output-available", output: {} }], true],
  ["streaming", [text("streaming")], false],
  ["streaming", [{ ...call, state: "input-available" }], false],
  ["ready", [text("done")], false],
] as const)("tells whether %s with %j shows Processing: %s", (status, parts, shown) => {
  expect(isProcessing([assistant(...parts)], status)).toBe(shown);
});

it("shows the live run error over the stored one of the last message", () => {
  const stored = [msg("user", "q", { chatError: "provider exploded" })];

  expect(activeChatError(stored, undefined)).toBe("provider exploded");
  expect(activeChatError(stored, new Error("died"))).toBe("died");
  expect(activeChatError([msg("assistant", "all good")], undefined)).toBeUndefined();
});

it("tells the last message while it streams or waits from any earlier one", () => {
  const messages = [msg("user", "q"), msg("assistant", "a")];

  expect(liveOf(messages, "streaming", "assistant-a")).toBe("streaming");
  expect(liveOf(messages, "error", "assistant-a")).toBe("waiting");
  expect(liveOf(messages, "streaming", "user-q")).toBe("stale");
});

it("resends a message under a fresh id and keeps its attachments", () => {
  const image: Part = { type: "file", mediaType: "image/png", url: "data:image/png;base64,AAAA" };
  const sent: ChatMessage = {
    id: "u1",
    role: "user",
    parts: [{ type: "text", text: "look" }, image],
  };
  const resend = resendOf(sent, "look again");

  expect(resend).toMatchObject({
    messageId: "u1",
    role: "user",
    parts: [image, { type: "text", text: "look again" }],
  });
  expect(resend.id).not.toBe("u1");
});

describe("settleTurn", () => {
  const stored = [msg("user", "q", { chatError: "failed" }), msg("assistant", "stored")];

  it("replaces the streamed turn with its stored version, keeping what was sent after it", () => {
    const steering = msg("user", "next");
    const messages = [msg("assistant", "a"), msg("user", "q"), msg("assistant", "live"), steering];

    expect(settleTurn(messages, stored, "assistant-live")).toEqual([
      msg("assistant", "a"),
      ...stored,
      steering,
    ]);
  });

  it("gives up on a transcript that lacks the turn", () => {
    expect(settleTurn([msg("user", "other")], stored, "assistant-live")).toBeUndefined();
  });
});

it.each([true, false])("continues the run once its question is answered: %s", (answered) => {
  const ask = { type: "tool-ask_user", toolCallId: "ask-1", input: {} } as const;
  const part: Part = answered
    ? { ...ask, state: "output-available", output: [] }
    : { ...ask, state: "input-available" };

  expect(shouldContinueRun({ messages: [msg("user", "q"), assistant(part)] })).toBe(answered);
});

it("does not resend a stopped step of server tools", () => {
  const search: Part = {
    type: "tool-search",
    toolCallId: "s-1",
    state: "output-available",
    input: {},
    output: "",
  };

  expect(shouldContinueRun({ messages: [msg("user", "q"), assistant(search)] })).toBe(false);
});
