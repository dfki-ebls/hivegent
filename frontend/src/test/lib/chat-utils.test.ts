import { describe, expect, it } from "vitest";

import {
  type ChatMessage,
  activeChatError,
  adoptMessageNodeId,
  canCompact,
  isContextLengthError,
  recordChatError,
  showThinkingLoader,
} from "@/lib/chat/chat-utils";

const msg = (role: ChatMessage["role"], text: string): ChatMessage => ({
  id: `${role}-${text}`,
  role,
  parts: [{ type: "text", text }],
});

const assistant = (parts: ChatMessage["parts"]): ChatMessage[] => [
  { id: "assistant", role: "assistant", parts },
];

describe("isContextLengthError", () => {
  it("matches the backend's canonical overflow code", () => {
    expect(
      isContextLengthError(
        "context_length_exceeded: Model token limit (provider default) exceeded",
      ),
    ).toBe(true);
  });

  it("ignores raw provider messages and unrelated errors", () => {
    expect(isContextLengthError("This model's maximum context length is 8192 tokens.")).toBe(false);
    expect(isContextLengthError("connection refused")).toBe(false);
    expect(isContextLengthError(undefined)).toBe(false);
  });
});

describe("showThinkingLoader", () => {
  it("shows the loader while waiting for the first token", () => {
    expect(showThinkingLoader(assistant([]), "submitted")).toBe(true);
  });

  it("keeps the loader visible in the gap after a tool call", () => {
    const parts: ChatMessage["parts"] = [
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
    const parts: ChatMessage["parts"] = [{ type: "text", text: "hi", state: "streaming" }];
    expect(showThinkingLoader(assistant(parts), "streaming")).toBe(false);
  });

  it("hides the loader once the turn is ready", () => {
    const parts: ChatMessage["parts"] = [{ type: "text", text: "done", state: "done" }];
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

describe("adoptMessageNodeId", () => {
  it("re-keys the message the finished turn sent, not the answer to it", () => {
    const messages = [msg("user", "first"), msg("assistant", "reply"), msg("user", "second")];
    const adopted = adoptMessageNodeId(messages, "node-9");

    expect(adopted.map((m) => m.id)).toEqual(["user-first", "assistant-reply", "node-9"]);
    expect(adopted[2].parts).toBe(messages[2].parts);
  });

  it("leaves a turn without a user message alone", () => {
    const messages = [msg("assistant", "summary")];

    expect(adoptMessageNodeId(messages, "node-9")).toBe(messages);
  });
});

describe("activeChatError", () => {
  const stored: ChatMessage[] = [
    {
      id: "user",
      role: "user",
      parts: [{ type: "text", text: "q" }],
      metadata: { chatError: "provider exploded" },
    },
  ];

  it("surfaces a stored run error from the last message", () => {
    expect(activeChatError(stored, undefined)).toBe("provider exploded");
  });

  it("prefers the live error over the stored one", () => {
    expect(activeChatError(stored, new Error("still streaming when it died"))).toBe(
      "still streaming when it died",
    );
  });

  it("returns undefined when the last message carries no error", () => {
    expect(activeChatError([msg("assistant", "all good")], undefined)).toBeUndefined();
  });
});

describe("recordChatError", () => {
  it("carries a live error through the route handoff", () => {
    const messages = [msg("user", "q")];
    const recorded = recordChatError(messages, new Error("provider exploded"));

    expect(activeChatError(recorded, undefined)).toBe("provider exploded");
    expect(recordChatError(messages, undefined)).toBe(messages);
  });
});
