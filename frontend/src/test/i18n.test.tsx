import { render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { ConnectingScreen } from "@/components/ConnectingScreen";
import { browserLanguage, i18n } from "@/i18n";

afterEach(async () => {
  await i18n.changeLanguage("en");
});

it("renders the interface in German", async () => {
  await i18n.changeLanguage("de");
  render(<ConnectingScreen />);

  expect(screen.getByText("Verbindung zum Server wird hergestellt …")).toBeTruthy();
});

it("picks the first supported browser language by its base language", () => {
  expect(browserLanguage(["fr-FR", "de-AT", "en"])).toBe("de");
  expect(browserLanguage(["fr"])).toBe("en");
});
