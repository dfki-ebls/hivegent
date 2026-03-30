import { describe, expect, it } from "vitest";

import {
  normalizeDisplayMathDelimiters,
  normalizeMathDelimiters,
} from "@/lib/normalize-math";

describe("normalizeMathDelimiters", () => {
  it("converts inline \\(...\\) to $...$", () => {
    expect(normalizeMathDelimiters("The equation \\(x^2\\) is simple")).toBe(
      "The equation $x^2$ is simple"
    );
  });

  it("converts display \\[...\\] to $$...$$", () => {
    expect(normalizeMathDelimiters("\\[E = mc^2\\]")).toBe("$$E = mc^2$$");
  });

  it("handles multiline display math", () => {
    const input = "\\[\n  x^2 +\n  y^2 = z^2\n\\]";
    const expected = "$$\n  x^2 +\n  y^2 = z^2\n$$";
    expect(normalizeMathDelimiters(input)).toBe(expected);
  });

  it("handles multiple occurrences", () => {
    const input = "\\(a\\) and \\(b\\) and \\[c\\]";
    const expected = "$a$ and $b$ and $$c$$";
    expect(normalizeMathDelimiters(input)).toBe(expected);
  });

  it("leaves escaped backslashes untouched", () => {
    expect(normalizeMathDelimiters("\\\\(not math\\\\)")).toBe(
      "\\\\(not math\\\\)"
    );
  });

  it("leaves existing $...$ notation unchanged", () => {
    expect(normalizeMathDelimiters("$x^2$ and $$y^2$$")).toBe(
      "$x^2$ and $$y^2$$"
    );
  });

  it("handles nested LaTeX commands", () => {
    expect(normalizeMathDelimiters("\\(\\frac{1}{2}\\)")).toBe(
      "$\\frac{1}{2}$"
    );
  });

  it("returns plain text unchanged", () => {
    expect(normalizeMathDelimiters("no math here")).toBe("no math here");
  });

  it("returns empty string unchanged", () => {
    expect(normalizeMathDelimiters("")).toBe("");
  });

  it("handles mixed notation styles", () => {
    const input = "$a$ then \\(b\\) then $$c$$ then \\[d\\]";
    const expected = "$a$ then $b$ then $$c$$ then $$d$$";
    expect(normalizeMathDelimiters(input)).toBe(expected);
  });
});

describe("normalizeDisplayMathDelimiters", () => {
  it("converts display \\[...\\] to $$...$$", () => {
    expect(normalizeDisplayMathDelimiters("\\[E = mc^2\\]")).toBe(
      "$$E = mc^2$$"
    );
  });

  it("does not convert inline \\(...\\)", () => {
    expect(normalizeDisplayMathDelimiters("\\(x^2\\)")).toBe("\\(x^2\\)");
  });

  it("converts only display delimiters in mixed input", () => {
    const input = "\\(a\\) and \\[b\\]";
    const expected = "\\(a\\) and $$b$$";
    expect(normalizeDisplayMathDelimiters(input)).toBe(expected);
  });
});
