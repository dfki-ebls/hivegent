import { describe, expect, it } from "vitest";

import { blocksOf, type ChatPart } from "@/lib/chat/layout";

const search: ChatPart = {
  type: "dynamic-tool",
  toolName: "search",
  toolCallId: "s",
  state: "output-available",
  input: {},
  output: "",
};
const approval: ChatPart = {
  type: "dynamic-tool",
  toolName: "write_document",
  toolCallId: "w",
  state: "approval-requested",
  input: {},
  approval: { id: "w" },
};
const question: ChatPart = {
  type: "tool-ask_user",
  toolCallId: "ask",
  state: "input-available",
  input: {},
};

const kinds = (blocks: ReturnType<typeof blocksOf>) =>
  blocks.map((block) => (block.kind === "steps" ? block.parts.length : block.part.type));

describe("blocksOf", () => {
  it("groups consecutive steps into one run between texts", () => {
    const parts: ChatPart[] = [
      { type: "reasoning", text: "Hm", state: "done" },
      { type: "step-start" },
      search,
      { type: "text", text: "Found it." },
      search,
      { type: "text", text: " " },
    ];

    expect(kinds(blocksOf(parts, "waiting"))).toEqual([2, "text", 1]);
    expect(blocksOf(parts, "streaming").at(-1)).toMatchObject({ kind: "steps", running: true });
  });

  it("stands an open prompt on its own only while it can be answered", () => {
    expect(kinds(blocksOf([approval], "waiting"))).toEqual(["dynamic-tool"]);
    expect(kinds(blocksOf([approval], "stale"))).toEqual([1]);
    expect(kinds(blocksOf([question], "waiting"))).toEqual(["tool-ask_user"]);
    expect(kinds(blocksOf([question], "streaming"))).toEqual([1]);
  });
});
