import { describe, expect, it } from "vitest";

import { normalizeVoidTags } from "@/lib/normalize-void-tags";

describe("normalizeVoidTags", () => {
  it("closes an unclosed cite so it stops swallowing prose", () => {
    expect(normalizeVoidTags('a <cite src="~/x.md" line="42">. b')).toBe(
      'a <cite src="~/x.md" line="42"></cite>. b',
    );
  });

  it("normalizes a self-closing cite to an explicit empty element", () => {
    expect(normalizeVoidTags('<cite src="~/x.md" line="42,50-55" />')).toBe(
      '<cite src="~/x.md" line="42,50-55"></cite>',
    );
  });

  it("keeps slashes inside attribute values intact", () => {
    expect(normalizeVoidTags('<cite src="~/a/b.md" line="42">')).toBe(
      '<cite src="~/a/b.md" line="42"></cite>',
    );
  });

  it("voids a self-closing imgref", () => {
    expect(normalizeVoidTags('<imgref src="~/p.png" alt="a chart" />')).toBe(
      '<imgref src="~/p.png" alt="a chart"></imgref>',
    );
  });

  it("strips stray closing tags, leaving wrapped text as prose", () => {
    expect(normalizeVoidTags('<imgref src="~/p.png">cap</imgref>')).toBe(
      '<imgref src="~/p.png"></imgref>cap',
    );
  });

  it("detaches a cite glued to a closing fence so the block can close", () => {
    expect(normalizeVoidTags('```\ncode\n```<cite src="~/x.md" line="42" />')).toBe(
      '```\ncode\n```\n<cite src="~/x.md" line="42"></cite>',
    );
  });

  it("leaves an opening fence with a language info string untouched", () => {
    expect(normalizeVoidTags("```java\ncode\n```")).toBe("```java\ncode\n```");
  });

  it("returns text without void tags unchanged", () => {
    expect(normalizeVoidTags("no tags here")).toBe("no tags here");
  });
});
