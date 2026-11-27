import { expect, it } from "vitest";

import { legacySystemMessage } from "@/migrations/legacy-system-message";

const stored = (state: object) => JSON.stringify({ state, version: 0 });

it.each([
  [stored({ personality: "custom", customSystemMessage: " Be brief. " }), "Be brief."],
  [stored({ personality: "concise", customSystemMessage: "Be brief." }), undefined],
  [stored({ personality: "custom", customSystemMessage: " " }), undefined],
  [stored({ expandedItems: ["~"] }), undefined],
  ["{", undefined],
])("reads the custom system message of %s as %j", (persisted, expected) => {
  expect(legacySystemMessage(persisted)).toBe(expected);
});
