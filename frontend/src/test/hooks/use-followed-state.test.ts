import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useFollowedState } from "@/hooks/use-followed-state";
import { followEach } from "@/lib/utils";
import { publishWorkspaceChanged } from "@/stores/jobs-store";

describe("useFollowedState", () => {
  it("keeps a selection on what a change moved and drops what it deleted", () => {
    const { result } = renderHook(() =>
      useFollowedState(["~/a.md", "~/docs/b.md", "~/gone.md", "~/kept.md"], followEach),
    );

    act(() =>
      publishWorkspaceChanged({
        type: "workspace-changed",
        id: "move",
        scopes: ["@team", "~"],
        moves: [
          { source: "~/a.md", destination: "~/archive/a.md", is_dir: false, replaces: false },
          { source: "~/docs", destination: "@team/docs", is_dir: true, replaces: false },
        ],
        deletes: ["~/gone.md"],
      }),
    );

    expect(result.current[0]).toEqual(["~/archive/a.md", "@team/docs/b.md", "~/kept.md"]);
  });
});
