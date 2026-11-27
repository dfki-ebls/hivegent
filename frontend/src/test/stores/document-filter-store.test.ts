import { expect, it } from "vitest";

import type { WorkspaceChanged } from "@/api/jobs";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { publishWorkspaceChanged } from "@/stores/jobs-store";

const doc = (path: string) => ({ path, directory: false });
const folder = (path: string) => ({ path, directory: true });

const move = (source: string, destination: string, is_dir = false) => ({
  source,
  destination,
  is_dir,
  replaces: false,
});

it("follows a workspace change spanning two scopes at once", () => {
  useDocumentFilterStore.setState({
    included: [folder("~/docs")],
    excluded: ["~/a.md", "@team/a.md", "~/docs/x.md", "~/docs-old/x.md", "~/gone/y.md"].map(doc),
  });
  const changed: WorkspaceChanged = {
    type: "workspace-changed",
    id: "swap",
    scopes: ["@team", "~"],
    moves: [
      move("~/a.md", "@team/a.md"),
      move("@team/a.md", "~/a.md"),
      move("~/docs", "~/kept/docs", true),
    ],
    deletes: ["~/gone"],
  };

  publishWorkspaceChanged(changed);

  expect(useDocumentFilterStore.getState().included).toEqual([folder("~/kept/docs")]);
  expect(useDocumentFilterStore.getState().excluded).toEqual(
    ["@team/a.md", "~/a.md", "~/kept/docs/x.md", "~/docs-old/x.md"].map(doc),
  );
});
