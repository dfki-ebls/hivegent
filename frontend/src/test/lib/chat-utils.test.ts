import type { UIMessage } from "@ai-sdk/react";
import { describe, expect, it } from "vitest";

import { canCompact, isContextLengthError, showThinkingLoader } from "@/lib/chat/chat-utils";

const msg = (role: UIMessage["role"], text: string): UIMessage => ({
  id: `${role}-${text}`,
  role,
  parts: [{ type: "text", text }],
});

const assistant = (parts: UIMessage["parts"]): UIMessage[] => [
  { id: "assistant", role: "assistant", parts },
];

describe("isContextLengthError", () => {
  it("matches the backend's canonical overflow code", () => {
    expect(
      isContextLengthError(
        new Error("context_length_exceeded: Model token limit (provider default) exceeded"),
      ),
    ).toBe(true);
  });

  it("ignores raw provider messages and unrelated errors", () => {
    expect(
      isContextLengthError(new Error("This model's maximum context length is 8192 tokens.")),
    ).toBe(false);
    expect(isContextLengthError(new Error("connection refused"))).toBe(false);
    expect(isContextLengthError(undefined)).toBe(false);
  });
});

describe("showThinkingLoader", () => {
  it("shows the loader while waiting for the first token", () => {
    expect(showThinkingLoader(assistant([]), "submitted")).toBe(true);
  });

  it("keeps the loader visible in the gap after a tool call", () => {
    const parts: UIMessage["parts"] = [
      {
        type: "dynamic-tool",
        toolName: "search",
        toolCallId: "1",
        state: "output-available",
        input: {},
        output: {},
      },
    ];
    expect(showThinkingLoader(assistant(parts), "streaming")).toBe(true);
  });

  it("hides the loader while text is actively streaming", () => {
    const parts: UIMessage["parts"] = [{ type: "text", text: "hi", state: "streaming" }];
    expect(showThinkingLoader(assistant(parts), "streaming")).toBe(false);
  });

  it("hides the loader once the turn is ready", () => {
    const parts: UIMessage["parts"] = [{ type: "text", text: "done", state: "done" }];
    expect(showThinkingLoader(assistant(parts), "ready")).toBe(false);
  });
});

describe("canCompact", () => {
  it("blocks a lone oversized user turn", () => {
    expect(canCompact([msg("user", "huge file")])).toBe(false);
  });

  it("blocks a freshly compacted conversation (summary + one new turn)", () => {
    expect(canCompact([msg("assistant", "summary"), msg("user", "huge file")])).toBe(false);
  });

  it("allows compaction once there is a prior user turn to compress", () => {
    expect(
      canCompact([msg("user", "first"), msg("assistant", "reply"), msg("user", "second")]),
    ).toBe(true);
  });
});
