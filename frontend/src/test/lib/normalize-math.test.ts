import { expect, it } from "vitest";

import { normalizeDisplayMath, normalizeMath } from "@/lib/normalize-math";

it.each([
  ["\\(x^2\\) and \\(\\frac{1}{2}\\)", "$x^2$ and $\\frac{1}{2}$"],
  ["\\[E = mc^2\\] and \\[\n  x\n\\]", "$$E = mc^2$$ and $$\n  x\n$$"],
  ["$a$ then \\(b\\) then $$c$$ then \\[d\\]", "$a$ then $b$ then $$c$$ then $$d$$"],
  ["\\\\(not math\\\\) a line\\\\and prose", "\\\\(not math\\\\) a line\\\\and prose"],
  ["see $\\\\alpha$ and $$\\\\frac{a}{b}$$", "see $\\alpha$ and $$\\frac{a}{b}$$"],
  [
    "$$\\begin{matrix} a & b \\\\ c & d \\end{matrix}$$",
    "$$\\begin{matrix} a & b \\\\ c & d \\end{matrix}$$",
  ],
  ["no math here", "no math here"],
])("normalizes %j to %j", (input, expected) => {
  expect(normalizeMath(input)).toBe(expected);
});

it("converts only display math in a document that renders inline math itself", () => {
  expect(normalizeDisplayMath("\\[a\\] \\(b\\) $$\\\\c$$")).toBe("$$a$$ \\(b\\) $$\\c$$");
});
