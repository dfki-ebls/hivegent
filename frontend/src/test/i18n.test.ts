import { expect, it } from "vitest";

import { browserLanguage } from "@/i18n";

it("picks the first supported browser language by its base language", () => {
  expect(browserLanguage(["fr-FR", "de-AT", "en"])).toBe("de");
  expect(browserLanguage(["fr"])).toBe("en");
});
