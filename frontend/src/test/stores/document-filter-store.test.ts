import { describe, expect, it, vi } from "vitest";

import type { subscribeJobs as subscribeJobsFn } from "@/lib/api";
import type { ChangesetCommitted } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  subscribeJobs: vi.fn<typeof subscribeJobsFn>(),
}));

import { subscribeJobs } from "@/lib/api";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { useJobsStore } from "@/stores/jobs-store";

const move = (source: string, destination: string, is_dir = false) => ({
  source,
  destination,
  is_dir,
  replaces: false,
});

describe("the document filter and the feed", () => {
  it("follows a commit spanning two scopes at once", async () => {
    useDocumentFilterStore.setState({
      included: ["~/docs/"],
      excluded: ["~/a.md", "@team/a.md", "~/docs/x.md", "~/docs-old/x.md", "~/gone/y.md"],
    });
    const commit: ChangesetCommitted = {
      type: "changeset-committed",
      scopes: ["@team", "~"],
      moves: [
        move("~/a.md", "@team/a.md"),
        move("@team/a.md", "~/a.md"),
        move("~/docs", "~/kept/docs", true),
      ],
      deletes: ["~/gone"],
    };
    vi.mocked(subscribeJobs).mockImplementation(async (_onJob, _onReady, _onScope, onCommitted) => {
      onCommitted(commit);
      await new Promise<never>(() => {});
    });

    useJobsStore.getState().start();

    await vi.waitFor(() =>
      expect(useDocumentFilterStore.getState().included).toEqual(["~/kept/docs/"]),
    );
    expect(useDocumentFilterStore.getState().excluded).toEqual([
      "@team/a.md",
      "~/a.md",
      "~/kept/docs/x.md",
      "~/docs-old/x.md",
    ]);
  });
});
