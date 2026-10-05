import { afterEach, beforeAll, vi } from "vitest";

import { i18n } from "@/i18n";

// Assertions read the English catalog, whatever language jsdom reports.
beforeAll(async () => {
  await i18n.changeLanguage("en");
});

afterEach(() => {
  vi.restoreAllMocks();
});
