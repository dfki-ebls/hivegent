import { describe, expect, it } from "vitest";

import type { DirectoryEntry, DirectoryTreeResponse } from "@/api/documents";
import {
  buildTreeIndex,
  entriesOf,
  filterChildren,
  getNode,
  planMoves,
  uploadTarget,
} from "@/lib/document-tree";

const file = (path: string): DirectoryEntry => ({
  type: "file",
  name: path.slice(path.lastIndexOf("/") + 1),
  path,
});

const dir = (path: string, children: DirectoryEntry[]): DirectoryEntry => ({
  type: "directory",
  name: path.slice(path.lastIndexOf("/") + 1),
  path,
  children,
});

const tree = (...children: DirectoryEntry[]): DirectoryTreeResponse => ({
  root: dir("", children),
  total_files: 0,
  total_directories: 0,
});

const index = buildTreeIndex([
  {
    scope: "~",
    kind: "user",
    label: "Me",
    writable: true,
    tree: tree(
      file("z.md"),
      dir("docs", [file("docs/a.md"), dir("docs/old", [file("docs/old/b.md")])]),
    ),
  },
  { scope: "@team", kind: "group", label: "Team", writable: false, tree: tree(file("t.md")) },
]);

describe("buildTreeIndex", () => {
  it("keys every entry by canonical path below its workspace, folders first", () => {
    expect(getNode(index, "").children).toEqual(["~", "@team"]);
    expect(getNode(index, "~").children).toEqual(["~/docs", "~/z.md"]);
    expect(getNode(index, "~/docs")).toMatchObject({ kind: "directory", fileCount: 2 });
  });

  it("keeps only matches and the folders leading to them", () => {
    const children = filterChildren(index, new Set(["~/docs/a.md"]));

    expect(Object.fromEntries(children)).toEqual({
      "": ["~"],
      "~": ["~/docs"],
      "~/docs": ["~/docs/a.md"],
      "~/docs/old": [],
      "@team": [],
    });
  });
});

describe("selection and moves", () => {
  it("resolves a selection to its outermost entries, a workspace to its contents", () => {
    const ids = entriesOf(index, ["~/docs", "~/docs/old/b.md", "@team"]).map((node) => node.id);

    expect(ids).toEqual(["~/docs", "@team/t.md"]);
  });

  it("moves entries into a folder by name, leaving those already there", () => {
    const entries = entriesOf(index, ["~/docs/old/b.md", "@team/t.md", "~/docs/a.md"]);
    const { paths, destination } = planMoves(entries, "~/docs");

    expect(paths).toEqual(["~/docs/old/b.md", "@team/t.md"]);
    expect(paths.map(destination)).toEqual(["~/docs/b.md", "~/docs/t.md"]);
  });

  it("never moves a folder into itself or its own subtree", () => {
    const docs = entriesOf(index, ["~/docs"]);

    expect(planMoves(docs, "~/docs").paths).toEqual([]);
    expect(planMoves(docs, "~/docs/old").paths).toEqual([]);
    expect(planMoves(docs, "@team").paths).toEqual(["~/docs"]);
  });

  it("targets the focused folder, a file's folder, or the personal root when read-only", () => {
    expect(uploadTarget(index, "~/docs/old")).toBe("~/docs/old");
    expect(uploadTarget(index, "~/docs/a.md")).toBe("~/docs");
    expect(uploadTarget(index, "@team/t.md")).toBe("~");
  });
});
