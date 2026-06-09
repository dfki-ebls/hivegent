import type { UIMessage } from "@ai-sdk/react";
import { describe, expect, it } from "vitest";

import { canCompact } from "@/lib/chat/chat-utils";

const msg = (role: UIMessage["role"], text: string): UIMessage => ({
  id: `${role}-${text}`,
  role,
  parts: [{ type: "text", text }],
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
