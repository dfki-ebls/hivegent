import { expect, it } from "vitest";

import { isWebUrl } from "@/lib/utils";

it("takes only an absolute http or https URL as a web URL", () => {
  expect(isWebUrl("https://example.com/a")).toBe(true);
  expect(isWebUrl("http://example.com")).toBe(true);
  expect(isWebUrl("http-notes/a.md")).toBe(false);
  expect(isWebUrl("ftp://example.com")).toBe(false);
});
