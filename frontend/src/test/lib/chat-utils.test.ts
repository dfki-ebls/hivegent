import { describe, expect, it } from "vitest";

import {
  type ChatMessage,
  activeChatError,
  adoptMessageNodeId,
  closeAbandonedCalls,
  getLastUserMessage,
  placeCompactions,
  recordChatError,
  shouldContinueRun,
  isProcessing,
} from "@/lib/chat/chat-utils";

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

describe("getLastUserMessage", () => {
  it("carries the attachments a resend has to send again", () => {
    const messages: ChatMessage[] = [
      {
        id: "u1",
        role: "user",
        parts: [{ type: "text", text: "look" }, image],
      },
    ];
    expect(getLastUserMessage(messages)).toEqual({
      id: "u1",
      text: "look",
      files: [image],
    });
  });

  it("treats an attachment-only message as a turn", () => {
    // It never reaches the server on a failed turn, so a resend that skipped
    // it (as an empty `text` once did) would drop the user's request entirely.
    const messages: ChatMessage[] = [{ id: "u1", role: "user", parts: [image] }];
    expect(getLastUserMessage(messages)).toEqual({
      id: "u1",
      text: "",
      files: [image],
    });
  });

  it("has nothing to resend for an empty message", () => {
    expect(getLastUserMessage([{ id: "u1", role: "user", parts: [] }])).toBeUndefined();
    expect(getLastUserMessage([])).toBeUndefined();
  });
});

describe("closeAbandonedCalls", () => {
  const pending = (): ChatMessage => ({
    id: "a1",
    role: "assistant",
    parts: [
      {
        type: "dynamic-tool",
        toolName: "write_document",
        toolCallId: "call-1",
        state: "approval-requested",
        input: {},
        approval: { id: "call-1" },
      },
    ],
  });

  it("leaves a request the user can still answer alone", () => {
    const messages = [msg("user", "q"), pending()];

    expect(closeAbandonedCalls(messages)).toBe(messages);
  });

  it("closes a request the next message overtook, as the backend does", () => {
    const messages = [msg("user", "q"), pending(), msg("user", "never mind, do this instead")];
    const declined = closeAbandonedCalls(messages);
    const part = declined[1].parts[0];

    expect(part).toMatchObject({ state: "output-denied", approval: { approved: false } });
    // Untouched messages keep their identity, so nothing else re-renders.
    expect(declined[0]).toBe(messages[0]);
  });

  it("dismisses a question the next message overtook, as the backend does", () => {
    const messages = [msg("user", "q"), asked(), msg("user", "skip that")];

    expect(closeAbandonedCalls(messages)[1].parts[1]).toMatchObject({ state: "output-error" });
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

    expect(placed[0].parts.at(-1)).toBe(compaction);
    expect(placed[2].parts).not.toContain(compaction);
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
