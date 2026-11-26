import { describe, expect, it } from "vitest";

import { legacySystemMessage } from "@/migrations/legacy-system-message";

const stored = (state: object) => JSON.stringify({ state, version: 0 });

describe("legacySystemMessage", () => {
  it("keeps a chosen custom system message", () => {
    expect(
      legacySystemMessage(stored({ personality: "custom", customSystemMessage: " Be brief. " })),
    ).toBe("Be brief.");
  });

  it("ignores built-in personalities, an empty message, and garbage", () => {
    expect(
      legacySystemMessage(stored({ personality: "concise", customSystemMessage: "Be brief." })),
    ).toBeUndefined();
    expect(
      legacySystemMessage(stored({ personality: "custom", customSystemMessage: " " })),
    ).toBeUndefined();
    expect(legacySystemMessage(stored({ expandedItems: ["~"] }))).toBeUndefined();
    expect(legacySystemMessage("{")).toBeUndefined();
  });
});
