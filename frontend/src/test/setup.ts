import { beforeAll } from "vitest";

import { i18n } from "@/i18n";

// Assertions read the English catalog, whatever language jsdom reports.
beforeAll(async () => {
  await i18n.changeLanguage("en");
});

// jsdom implements no Web Animations, which Base UI waits on before it settles a scroll area.
if (!("getAnimations" in Element.prototype)) {
  Object.defineProperty(Element.prototype, "getAnimations", { value: () => [] });
}
