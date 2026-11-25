import { isToolUIPart, readUIMessageStream, simulateReadableStream, type UIMessageChunk } from "ai";
import { describe, expect, it } from "vitest";
import { parseMessages } from "@/api/conversations";
import { settleTurn } from "@/lib/chat/chat-utils";
import { type ChatMessage, messageSchemas } from "@/lib/chat/message";
import { type ToolPart, toolLineState } from "@/lib/chat/tool-part";

/**
 * Golden turns `backend/tests/unit/test_turn_parity.py` records: the messages a client
 * held, and the chunks its turn streamed, ending on the stored turn it swaps in.
 */
interface Fixture {
  initial: unknown[];
  chunks: UIMessageChunk[];
}

const fixtures = import.meta.glob<Fixture>("../fixtures/turns/*.json", {
  eager: true,
  import: "default",
});

/** Fields that tell a provider or the SDK something but change nothing on screen. */
const UNSEEN = new Set([
  "providerMetadata",
  "callProviderMetadata",
  "resultProviderMetadata",
  "providerExecuted",
]);

/** What *part*, a call, shows once its message is done: its line, its input and its result. */
function callOnScreen(part: ToolPart) {
  return {
    type: part.type,
    toolCallId: part.toolCallId,
    input: part.input,
    line: toolLineState(part, "stale"),
    output: part.state === "output-available" ? part.output : undefined,
    errorText: part.state === "output-error" ? part.errorText : undefined,
  };
}

/**
 * What *messages* show. A text's `state` only says whether more may stream, a reasoning
 * part's `id` is that of its stream, a call shows its line rather than its state, and the
 * run error shows as one banner whether the stream or the stored message carries it.
 */
function onScreen(messages: ChatMessage[]) {
  return messages.map(({ metadata, parts, ...message }) => {
    const { pydantic_ai: _, chatError: __, ...shown } = (metadata ?? {}) as Record<string, unknown>;

    return {
      ...message,
      metadata: shown,
      parts: parts.map((part) =>
        isToolUIPart(part)
          ? callOnScreen(part)
          : Object.fromEntries(
              Object.entries(part).filter(
                ([key]) =>
                  !UNSEEN.has(key) &&
                  !(key === "state" && "text" in part) &&
                  !(key === "id" && part.type === "reasoning"),
              ),
            ),
      ),
    };
  });
}

/** The transcript the AI SDK holds after streaming *chunks* into *initial*, up to an error. */
async function stream(initial: ChatMessage[], chunks: UIMessageChunk[]): Promise<ChatMessage[]> {
  const last = initial.at(-1);
  const continued = last?.role === "assistant" ? last : undefined;
  const error = chunks.findIndex((chunk) => chunk.type === "error");
  let streamed: ChatMessage | undefined;

  for await (const message of readUIMessageStream<ChatMessage>({
    message: continued,
    stream: simulateReadableStream({
      chunks: error === -1 ? chunks : chunks.slice(0, error),
      initialDelayInMs: null,
      chunkDelayInMs: null,
    }),
  })) {
    streamed = message;
  }

  return [...(continued ? initial.slice(0, -1) : initial), ...(streamed ? [streamed] : [])];
}

describe.each(Object.entries(fixtures))("%s", (_name, { initial, chunks }) => {
  it("streams only the data parts the client declares", () => {
    const declared = Object.keys(messageSchemas.dataSchemas).map((name) => `data-${name}`);

    const streamed = chunks.map(({ type }) => type).filter((type) => type.startsWith("data-"));

    expect(declared).toEqual(expect.arrayContaining(streamed));
  });

  it("ends on the turn a reload shows, ids included", async () => {
    const live = await stream(await parseMessages(initial), chunks);
    const turn = chunks.find((chunk) => chunk.type === "data-turn");
    const stored = await parseMessages(turn && "data" in turn ? (turn.data as unknown[]) : []);
    const reply = live.at(-1);

    expect(reply).toBeDefined();

    const settled = settleTurn(live, stored, reply?.id ?? "");

    expect(settled).toBeDefined();
    expect(onScreen(settled ?? [])).toEqual(onScreen(live));
  });
});
