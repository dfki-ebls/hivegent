import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { type ChangeOperation, type DirectoryTreeResponse, documentQueries } from "@/api/documents";
import type { WorkspaceChanged } from "@/api/jobs";
import type { Workspace } from "@/lib/document-tree";
import { useDocumentTree } from "@/hooks/use-document-tree";
import { DocumentTreeView } from "@/components/documents/document-tree";
import { createQueryWrapper, createTestQueryClient } from "@/test/render";

const { applyChanges, served } = vi.hoisted(() => ({
  applyChanges: vi.fn<(operations: ChangeOperation[]) => Promise<WorkspaceChanged>>(),
  served: { tree: undefined as DirectoryTreeResponse | undefined },
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

// The tree refetch answers with the served tree, the personal one unless a test changes it,
// and a change is recorded.
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
      : Promise.resolve(served.tree ?? personal),
}));

const workspaces: Workspace[] = [{ scope: "~", kind: "user", label: "Me", writable: true }];

function Harness() {
  const docTree = useDocumentTree({ workspaces, searchQuery: "", onUpload: () => {} });

  return <DocumentTreeView docTree={docTree} />;
}

function renderTree() {
  const client = createTestQueryClient();
  client.setQueryData(documentQueries.tree("~").queryKey, personal);

  return { client, ...render(<Harness />, { wrapper: createQueryWrapper(client) }) };
}

describe("DocumentTreeView", () => {
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
    act(() => {
      fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    });

    await waitFor(() =>
      expect(applyChanges).toHaveBeenCalledWith([
        { kind: "move", source: "~/docs/a.md", destination: "~/docs/b.md" },
      ]),
    );
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

    served.tree = { ...personal, root: { ...personal.root, children: [] } };
    client.setQueryData(documentQueries.tree("~").queryKey, served.tree);

    await waitFor(() => expect(screen.queryByRole("treeitem", { name: "docs" })).toBeNull());
    fireEvent.keyDown(screen.getByRole("treeitem", { name: "Me" }), {
      key: "Enter",
      code: "Enter",
    });
    served.tree = undefined;
  });
});
