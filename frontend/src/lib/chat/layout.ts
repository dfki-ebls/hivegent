import { isToolUIPart } from "ai";
import { ASK_USER_PART } from "@/lib/chat/ask-user";
import type { ChatMessage } from "@/lib/chat/message";
import type { Live, ToolPart } from "@/lib/chat/tool-part";

export type ChatPart = ChatMessage["parts"][number];

/**
 * Whether a call asks the user something they can still answer: an approval,
 * or a question of `ask_user` while the chat waits for its answer.
 */
export function isOpenPrompt(part: ToolPart, live: Live): boolean {
  if (part.state === "approval-requested") return live !== "stale";

  return part.type === ASK_USER_PART && part.state === "input-available" && live === "waiting";
}

/**
 * Where a part of a reply goes: a step the agent took sits in a trace with its
 * neighbors, content and anything asking the user stands on its own, and the
 * rest renders nothing.
 */
function partLayout(part: ChatPart, live: Live): "step" | "block" | null {
  switch (part.type) {
    case "text":
      return part.text.trim() ? "block" : null;
    case "reasoning":
      return part.text || (live === "streaming" && part.state === "streaming") ? "step" : null;
    case "file":
      return part.mediaType.startsWith("image/") ? "block" : null;
    case "data-compaction":
      return "block";
  }

  if (!isToolUIPart(part)) return null;

  return isOpenPrompt(part, live) ? "block" : "step";
}

type Block =
  /** `running` while the agent still works on the run, which nothing follows yet. */
  | { kind: "steps"; index: number; parts: ChatPart[]; running: boolean }
  | { kind: "block"; index: number; part: ChatPart };

/** The parts of a reply, where each run of steps between two blocks forms one chain of thought. */
export function blocksOf(parts: readonly ChatPart[], live: Live): Block[] {
  const blocks: Block[] = [];

  parts.forEach((part, index) => {
    const layout = partLayout(part, live);
    const last = blocks.at(-1);

    if (layout === "block") {
      blocks.push({ kind: "block", index, part });
    } else if (layout === "step" && last?.kind === "steps") {
      last.parts.push(part);
    } else if (layout === "step") {
      blocks.push({ kind: "steps", index, parts: [part], running: false });
    }
  });

  const last = blocks.at(-1);

  if (last?.kind === "steps") last.running = live === "streaming";

  return blocks;
}
