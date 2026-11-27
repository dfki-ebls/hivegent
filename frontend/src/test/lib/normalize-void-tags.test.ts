import { expect, it } from "vitest";

import { normalizeVoidTags, stripVoidTags } from "@/lib/normalize-void-tags";

it.each([
  ['a <cite src="~/x.md" line="42">. b', 'a <cite src="~/x.md" line="42"></cite>. b'],
  ['<cite src="~/a/b.md" line="42,50-55" />', '<cite src="~/a/b.md" line="42,50-55"></cite>'],
  ['<imgref src="~/p.png" alt="a chart" />', '<imgref src="~/p.png" alt="a chart"></imgref>'],
  ['<imgref src="~/p.png">cap</imgref>', '<imgref src="~/p.png"></imgref>cap'],
  [
    '```\ncode\n```<cite src="~/x.md" line="42" />',
    '```\ncode\n```\n<cite src="~/x.md" line="42"></cite>',
  ],
  ["```java\ncode\n```", "```java\ncode\n```"],
  ["no tags here", "no tags here"],
])("closes the void tags of %j", (input, expected) => {
  expect(normalizeVoidTags(input)).toBe(expected);
});

it("drops citations and image references for the clipboard", () => {
  expect(stripVoidTags('Revenue grew <cite src="a.md" line="3"/>.\n\n<imgref src="b.png">')).toBe(
    "Revenue grew.\n\n",
  );
});
