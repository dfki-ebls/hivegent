import { describe, expect, it } from "vitest";

import { normalizeDisplayMath, normalizeMath } from "@/lib/normalize-math";

describe("normalizeMath", () => {
  it("converts inline \\(...\\) to $...$", () => {
    expect(normalizeMath("The equation \\(x^2\\) is simple")).toBe(
      "The equation $x^2$ is simple",
    );
  });

  it("converts display \\[...\\] to $$...$$", () => {
    expect(normalizeMath("\\[E = mc^2\\]")).toBe("$$E = mc^2$$");
  });

  it("handles multiline display math", () => {
    const input = "\\[\n  x^2 +\n  y^2 = z^2\n\\]";
    const expected = "$$\n  x^2 +\n  y^2 = z^2\n$$";
    expect(normalizeMath(input)).toBe(expected);
  });

  it("handles multiple occurrences", () => {
    expect(normalizeMath("\\(a\\) and \\(b\\) and \\[c\\]")).toBe("$a$ and $b$ and $$c$$");
  });

  it("leaves escaped backslashes untouched", () => {
    expect(normalizeMath("\\\\(not math\\\\)")).toBe("\\\\(not math\\\\)");
  });

  it("leaves existing $...$ notation unchanged", () => {
    expect(normalizeMath("$x^2$ and $$y^2$$")).toBe("$x^2$ and $$y^2$$");
  });

  it("handles nested LaTeX commands", () => {
    expect(normalizeMath("\\(\\frac{1}{2}\\)")).toBe("$\\frac{1}{2}$");
  });

  it("returns plain text unchanged", () => {
    expect(normalizeMath("no math here")).toBe("no math here");
  });

  it("returns empty string unchanged", () => {
    expect(normalizeMath("")).toBe("");
  });

  it("handles mixed notation styles", () => {
    expect(normalizeMath("$a$ then \\(b\\) then $$c$$ then \\[d\\]")).toBe(
      "$a$ then $b$ then $$c$$ then $$d$$",
    );
  });

  it("collapses over-escaped commands inside display math", () => {
    const input =
      "$$\\\\text{Organischer N} \\\\xrightarrow{\\\\text{Ammonifikation}} NH_4^+ \\\\uparrow$$";
    const expected =
      "$$\\text{Organischer N} \\xrightarrow{\\text{Ammonifikation}} NH_4^+ \\uparrow$$";
    expect(normalizeMath(input)).toBe(expected);
  });

  it("collapses over-escaped commands inside inline math", () => {
    expect(normalizeMath("see $\\\\alpha + \\\\beta$ here")).toBe("see $\\alpha + \\beta$ here");
  });

  it("preserves row separators outside math", () => {
    expect(normalizeMath("a line\\\\and prose")).toBe("a line\\\\and prose");
  });

  it("keeps \\\\ row separators in matrices", () => {
    expect(normalizeMath("$$\\begin{matrix} a & b \\\\ c & d \\end{matrix}$$")).toBe(
      "$$\\begin{matrix} a & b \\\\ c & d \\end{matrix}$$",
    );
  });
});

describe("normalizeDisplayMath", () => {
  it("converts display \\[...\\] to $$...$$", () => {
    expect(normalizeDisplayMath("\\[E = mc^2\\]")).toBe("$$E = mc^2$$");
  });

  it("does not convert inline \\(...\\)", () => {
    expect(normalizeDisplayMath("\\(x^2\\)")).toBe("\\(x^2\\)");
  });

  it("collapses over-escaped commands inside display math", () => {
    expect(normalizeDisplayMath("$$\\\\frac{a}{b}$$")).toBe("$$\\frac{a}{b}$$");
  });
});
