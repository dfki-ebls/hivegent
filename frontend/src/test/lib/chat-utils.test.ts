import { describe, expect, it } from "vitest";

import {
  activeChatError,
  isProcessing,
  liveOf,
  placeCompactions,
  resendOf,
  shouldContinueRun,
} from "@/lib/chat/chat-utils";
import type { ChatMessage } from "@/lib/chat/message";

const image: ChatMessage["parts"][number] = {
  type: "file",
  mediaType: "image/png",
  url: "data:image/png;base64,AAAA",
};

const msg = (role: ChatMessage["role"], text: string): ChatMessage => ({
  id: `${role}-${text}`,
  role,
  parts: [{ type: "text", text }],
});

const assistant = (parts: ChatMessage["parts"]): ChatMessage[] => [
  { id: "assistant", role: "assistant", parts },
];

describe("isProcessing", () => {
  const call = { type: "dynamic-tool", toolName: "search", toolCallId: "1", input: {} } as const;

  it("shows while waiting for the first token", () => {
    expect(isProcessing(assistant([]), "submitted")).toBe(true);
  });

  it("shows in the gap after a tool call", () => {
    expect(
      isProcessing(assistant([{ ...call, state: "output-available", output: {} }]), "streaming"),
    ).toBe(true);
  });

  it("hides while text streams or a call runs", () => {
    const parts: ChatMessage["parts"] = [{ type: "text", text: "hi", state: "streaming" }];
    expect(isProcessing(assistant(parts), "streaming")).toBe(false);
    expect(isProcessing(assistant([{ ...call, state: "input-available" }]), "streaming")).toBe(
      false,
    );
  });

  it("hides once the turn is ready", () => {
    const parts: ChatMessage["parts"] = [{ type: "text", text: "done", state: "done" }];
    expect(isProcessing(assistant(parts), "ready")).toBe(false);
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

describe("liveOf", () => {
  const messages = [msg("user", "q"), msg("assistant", "a")];

  it("tells the last message while it streams or waits from any earlier one", () => {
    expect(liveOf(messages, "streaming", "assistant-a")).toBe("streaming");
    expect(liveOf(messages, "error", "assistant-a")).toBe("waiting");
    expect(liveOf(messages, "streaming", "user-q")).toBe("stale");
  });
});

describe("resendOf", () => {
  it("replaces the message under a fresh id and keeps its attachments", () => {
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
});

describe("placeCompactions", () => {
  const compaction: ChatMessage["parts"][number] = {
    type: "data-compaction",
    data: { content: "the gist" },
  };
  const reply = (id: string, parts: ChatMessage["parts"]): ChatMessage => ({
    id,
    role: "assistant",
    parts,
  });

  it("shows a compaction made for a prompt ahead of it, as a reload does", () => {
    const answered = [
      msg("assistant", "a"),
      msg("user", "q"),
      reply("r", [{ type: "step-start" }, compaction, { type: "text", text: "b" }]),
    ];
    const placed = placeCompactions(answered);

    expect(placed[0]?.parts.at(-1)).toBe(compaction);
    expect(placed[2]?.parts).not.toContain(compaction);
    // A reply that held only the compaction goes, as it never reloads.
    expect(placeCompactions([...answered.slice(0, 2), reply("r", [compaction])])).toHaveLength(2);
  });

  it("leaves a compaction the reply streamed after its own content", () => {
    const messages = [msg("user", "q"), reply("r", [{ type: "text", text: "b" }, compaction])];

    expect(placeCompactions(messages)).toBe(messages);
  });
});

function asked(answered = false): ChatMessage {
  const call = { type: "tool-ask_user", toolCallId: "ask-1", input: {} } as const;

  return {
    id: "a1",
    role: "assistant",
    parts: [
      { type: "step-start" },
      answered
        ? { ...call, state: "output-available", output: [] }
        : { ...call, state: "input-available" },
    ],
  };
}

describe("shouldContinueRun", () => {
  it("continues once the user answered the questions the run ended on", () => {
    expect(shouldContinueRun({ messages: [msg("user", "q"), asked()] })).toBe(false);
    expect(shouldContinueRun({ messages: [msg("user", "q"), asked(true)] })).toBe(true);
  });

  it("does not resend a stopped step of server tools", () => {
    const stopped: ChatMessage = {
      id: "a1",
      role: "assistant",
      parts: [
        { type: "step-start" },
        {
          type: "tool-search",
          toolCallId: "s-1",
          state: "output-available",
          input: {},
          output: "",
        },
      ],
    };

    expect(shouldContinueRun({ messages: [msg("user", "q"), stopped] })).toBe(false);
  });
});
