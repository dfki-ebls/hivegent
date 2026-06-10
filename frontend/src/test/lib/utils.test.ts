import { describe, expect, it } from "vitest";

import { commonParentDir } from "@/lib/utils";

describe("commonParentDir", () => {
  it("returns the deepest shared parent directory", () => {
    expect(commonParentDir(["images/x.png", "images/y.png"])).toBe("images/");
    expect(commonParentDir(["a/b/x.md", "a/c/y.md"])).toBe("a/");
    expect(commonParentDir(["docs/report.md"])).toBe("docs/");
  });

  it("collapses to root when the selection spans it", () => {
    // A root-level file in the mix forces the common parent back to root
    // instead of flattening the subdirectory below it.
    expect(commonParentDir(["a.md", "images/x.png", "images/y.png"])).toBe("");
    expect(commonParentDir([])).toBe("");
  });
});
