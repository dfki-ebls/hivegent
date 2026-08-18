import { describe, expect, it } from "vitest";

import { isValidMove, type TreeItemDrag } from "@/lib/dnd";

describe("isValidMove", () => {
  const file = (...paths: string[]): TreeItemDrag => ({ scope: "~", kind: "file", paths });
  const dir = (path: string): TreeItemDrag => ({ scope: "~", kind: "directory", paths: [path] });

  it("rejects a move that would not relocate the entry", () => {
    expect(isValidMove(file("a/b/note.md"), "~", "a/b")).toBe(false);
    expect(isValidMove(file("note.md"), "~", "")).toBe(false);
    expect(isValidMove(dir("a/b"), "~", "a")).toBe(false);
  });

  it("accepts a move into another directory", () => {
    expect(isValidMove(file("a/b/note.md"), "~", "a")).toBe(true);
    expect(isValidMove(file("note.md"), "~", "a/b")).toBe(true);
    expect(isValidMove(dir("a/b"), "~", "")).toBe(true);
  });

  it("rejects dropping a directory into itself or its own subtree", () => {
    expect(isValidMove(dir("a/b"), "~", "a/b")).toBe(false);
    expect(isValidMove(dir("a/b"), "~", "a/b/c")).toBe(false);
  });

  it("accepts any destination in another workspace, no-op paths included", () => {
    expect(isValidMove(file("a/note.md"), "@team", "a")).toBe(true);
    expect(isValidMove(dir("a/b"), "@team", "a")).toBe(true);
  });

  it("accepts a multi-file drag when any one of its files relocates", () => {
    expect(isValidMove(file("a/one.md", "b/two.md"), "~", "a")).toBe(true);
    expect(isValidMove(file("a/one.md", "a/two.md"), "~", "a")).toBe(false);
  });
});
