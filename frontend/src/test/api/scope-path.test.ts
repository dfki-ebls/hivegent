import { describe, expect, it } from "vitest";

import { formatTarget, isWorkspacePath, splitScopePath } from "@/api/documents";

describe("scope paths", () => {
  it("splits every scope, the global one keeping its leading slash", () => {
    expect(splitScopePath("~/a/b")).toEqual({ scope: "~", local: "a/b" });
    expect(splitScopePath("@team")).toEqual({ scope: "@team", local: "" });
    expect(splitScopePath("/srv/a.md")).toEqual({ scope: "/srv", local: "a.md" });
    expect(splitScopePath("/mnt/wiki/a/b.md")).toEqual({ scope: "/mnt/wiki", local: "a/b.md" });
    expect(splitScopePath("/mnt/wiki")).toEqual({ scope: "/mnt/wiki", local: "" });
  });

  it("names the global workspace a workspace and /tmp none", () => {
    const workspaces = [
      { scope: "/srv", label: "Organisation" },
      { scope: "@team", label: "Team" },
      { scope: "/mnt/wiki", label: "wiki" },
    ];

    expect(isWorkspacePath("/srv/a.md", workspaces)).toBe(true);
    expect(isWorkspacePath("/tmp/a.md", workspaces)).toBe(false);
    expect(isWorkspacePath("/mnt/wiki/a.md", workspaces)).toBe(true);
    expect(formatTarget("/srv/docs", workspaces)).toBe("Organisation / docs");
    expect(formatTarget("@team/a", workspaces)).toBe("Team / a");
  });
});
