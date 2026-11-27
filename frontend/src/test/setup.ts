import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeAll, beforeEach, vi } from "vitest";

import { i18n } from "@/i18n";

// Assertions read the English catalog, whatever language jsdom reports.
beforeAll(async () => {
  await i18n.changeLanguage("en");
});

// Time only moves when a test advances it, so no outcome depends on how fast the machine is.
beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

// Cache notifications reach components as microtasks, which `act` drains, not as timers.
notifyManager.setScheduler(queueMicrotask);

// jsdom implements no Web Animations, which Base UI waits on before it settles a scroll area.
if (!("getAnimations" in Element.prototype)) {
  Object.defineProperty(Element.prototype, "getAnimations", { value: () => [] });
}
