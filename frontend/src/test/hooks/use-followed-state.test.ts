import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { followSet, useFollowedState } from "@/hooks/use-followed-state";
import { publishCommitted } from "@/stores/jobs-store";

describe("useFollowedState", () => {
  it("keeps a selection on what a commit moved and drops what it deleted", () => {
    const { result } = renderHook(() =>
      useFollowedState("~", new Set(["a.md", "docs/b.md", "gone.md", "kept.md"]), followSet),
    );

    act(() =>
      publishCommitted({
        moves: [
          { source: "~/a.md", destination: "~/archive/a.md", is_dir: false, replaces: false },
          { source: "~/docs", destination: "@team/docs", is_dir: true, replaces: false },
        ],
        deletes: ["~/gone.md"],
      }),
    );

    expect([...result.current[0]]).toEqual(["archive/a.md", "kept.md"]);
  });
});
