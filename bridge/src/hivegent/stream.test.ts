import { describe, expect, it, vi } from "vitest";

import { parseHivegentStream, type Reopen, type StreamEvent } from "./stream.js";

const sse = (chunks: string[]): string => chunks.map((c) => `data: ${c}\n\n`).join("");

async function collect(body: string): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];

  for await (const event of parseHivegentStream(new Response(body))) {
    events.push(event);
  }

  return events;
}

describe("parseHivegentStream", () => {
  it("extracts text-delta.delta, drops reasoning/framing, stops at [DONE]", async () => {
    const events = await collect(
      sse([
        '{"type":"start"}',
        '{"type":"start-step"}',
        '{"type":"reasoning-delta","id":"r","delta":"thinking"}',
        '{"type":"text-start","id":"t"}',
        '{"type":"text-delta","id":"t","delta":"Hel"}',
        '{"type":"text-delta","id":"t","delta":"lo"}',
        '{"type":"text-end","id":"t"}',
        '{"type":"finish","finishReason":"stop"}',
        "[DONE]",
        '{"type":"text-delta","id":"t","delta":"IGNORED"}',
      ]),
    );

    expect(events).toEqual([
      { kind: "text", text: "Hel" },
      { kind: "text", text: "lo" },
    ]);
  });

  it("maps tool-input-start to a status label", async () => {
    const events = await collect(
      sse(['{"type":"tool-input-start","toolCallId":"c","toolName":"search"}', "[DONE]"]),
    );

    expect(events).toEqual([{ kind: "status", label: "Searching documents…" }]);
  });

  it("surfaces error chunks", async () => {
    const events = await collect(sse(['{"type":"error","errorText":"boom"}', "[DONE]"]));

    expect(events).toEqual([{ kind: "error", text: "boom" }]);
  });

  it("follows a cut stream again, skipping the chunks already read", async () => {
    const chunks = [
      '{"type":"start","messageId":"m"}',
      '{"type":"text-delta","id":"t","delta":"Hel"}',
      '{"type":"text-delta","id":"t","delta":"lo"}',
      '{"type":"finish","finishReason":"stop"}',
    ];
    const reopen = vi.fn<Reopen>(async () => new Response(sse(chunks)));
    const events: StreamEvent[] = [];

    for await (const event of parseHivegentStream(new Response(sse(chunks.slice(0, 2))), reopen)) {
      events.push(event);
    }

    expect(reopen).toHaveBeenCalledOnce();
    expect(events).toEqual([
      { kind: "text", text: "Hel" },
      { kind: "text", text: "lo" },
    ]);
  });

  it("says the answer was cut off once the turn ended unseen", async () => {
    const events: StreamEvent[] = [];
    const cut = new Response(sse(['{"type":"text-delta","id":"t","delta":"Hel"}']));

    for await (const event of parseHivegentStream(cut, async () => null)) {
      events.push(event);
    }

    expect(events.at(-1)?.kind).toBe("error");
  });
});
