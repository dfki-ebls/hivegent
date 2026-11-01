import { describe, expect, it } from "vitest";

import { buildRequestBody } from "./client.js";

describe("buildRequestBody", () => {
  it("builds a schema-valid submit-message with one user text part and read-only tools", () => {
    const body = buildRequestBody("Hello", {
      reasoningEffort: "high",
      disabledTools: ["edit_document", "write_document", "write_memory"],
    });

    expect(body.trigger).toBe("submit-message");
    expect(typeof body.id).toBe("string");
    expect(body.mode).toBe("read");
    expect(body.reasoning_effort).toBe("high");
    expect(body.tools).toEqual({
      disabled_tools: ["edit_document", "write_document", "write_memory"],
    });

    const messages = body.messages as Array<{ id: string; role: string; parts: unknown[] }>;
    expect(messages).toHaveLength(1);
    expect(messages[0]?.role).toBe("user");
    // hivegent stores the message under its id, which must be a canonical UUID.
    expect(messages[0]?.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(messages[0]?.parts).toEqual([{ type: "text", text: "Hello" }]);
  });
});
