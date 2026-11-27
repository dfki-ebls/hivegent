import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type ChangeOperation, type DirectoryTreeResponse, documentQueries } from "@/api/documents";
import type { WorkspaceChanged } from "@/api/jobs";
import type { Workspace } from "@/lib/document-tree";
import { useDocumentTree } from "@/hooks/use-document-tree";
import { DocumentTreeView } from "@/components/documents/document-tree";
import { stubLayout } from "@/test/layout";
import { createQueryWrapper, createTestQueryClient, settle } from "@/test/render";

const { applyChanges } = vi.hoisted(() => ({
  applyChanges: vi.fn<(operations: ChangeOperation[]) => Promise<WorkspaceChanged>>(),
}));

const personal: DirectoryTreeResponse = {
  root: {
    type: "directory",
    name: "",
    path: "",
    children: [
      {
        type: "directory",
        name: "docs",
        path: "docs",
        children: [{ type: "file", name: "a.md", path: "docs/a.md" }],
      },
    ],
  },
  total_files: 1,
  total_directories: 2,
};

// The tree refetch answers with the personal tree and a change is recorded.
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  requestJson: (url: string, _error: string, _schema: unknown, init?: RequestInit) =>
    url.endsWith("/changes")
      ? applyChanges(
          (
            JSON.parse(typeof init?.body === "string" ? init.body : "") as {
              operations: ChangeOperation[];
            }
          ).operations,
        )
      : Promise.resolve(personal),
}));

const workspaces: Workspace[] = [{ scope: "~", kind: "user", label: "Me", writable: true }];

function Harness() {
  const docTree = useDocumentTree({ workspaces, searchQuery: "", onUpload: () => {} });
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null);

  return (
    <div ref={setScroller}>
      <DocumentTreeView docTree={docTree} scrollElement={scroller} />
    </div>
  );
}

function renderTree() {
  const client = createTestQueryClient();
  client.setQueryData(documentQueries.tree("~").queryKey, personal);

  return { client, ...render(<Harness />, { wrapper: createQueryWrapper(client) }) };
}

describe("DocumentTreeView", () => {
  beforeEach(() => stubLayout());

  it("expands a folder on click and renames an entry inline", async () => {
    applyChanges.mockResolvedValue({
      type: "workspace-changed",
      id: "rename",
      scopes: ["~"],
      moves: [
        { source: "~/docs/a.md", destination: "~/docs/b.md", is_dir: false, replaces: false },
      ],
      deletes: [],
    });
    renderTree();

    const docs = screen.getByRole("treeitem", { name: "docs" });
    expect(screen.queryByRole("treeitem", { name: "a.md" })).toBeNull();

    fireEvent.click(docs);
    const file = screen.getByRole("treeitem", { name: "a.md" });

    fireEvent.click(within(file).getByRole("button", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Rename" });
    fireEvent.change(input, { target: { value: "b.md" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    await settle();

    expect(applyChanges).toHaveBeenCalledWith([
      { kind: "move", source: "~/docs/a.md", destination: "~/docs/b.md" },
    ]);
  });

  it("selects only through the checkbox or a modified click", () => {
    renderTree();

    const docs = screen.getByRole("treeitem", { name: "docs" });
    const checkbox = within(docs).getByRole("checkbox", { name: "docs" });

    fireEvent.click(docs);
    fireEvent.click(docs);
    expect(checkbox.getAttribute("aria-checked")).toBe("false");

    fireEvent.click(docs, { ctrlKey: true });
    expect(checkbox.getAttribute("aria-checked")).toBe("true");
  });

  it("drops a selected and focused entry that vanished from a fresh tree", async () => {
    const { client } = renderTree();
    const docs = screen.getByRole("treeitem", { name: "docs" });
    fireEvent.click(docs, { ctrlKey: true });

    act(() => {
      client.setQueryData(documentQueries.tree("~").queryKey, {
        ...personal,
        root: { ...personal.root, children: [] },
      });
    });
    await settle();

    expect(screen.queryByRole("treeitem", { name: "docs" })).toBeNull();
    fireEvent.keyDown(screen.getByRole("treeitem", { name: "Me" }), {
      key: "Enter",
      code: "Enter",
    });
  });
});
