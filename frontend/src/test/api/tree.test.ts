import { type UseMutationOptions, useMutation, useQuery } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type ChangeOperation,
  type DirectoryTreeResponse,
  documentMutations,
  documentQueries,
  syncWorkspaceQueries,
} from "@/api/documents";
import type { WorkspaceChanged } from "@/api/jobs";

import { publishWorkspaceChanged } from "@/stores/jobs-store";
import { createQueryWrapper, createTestQueryClient } from "@/test/render";

// Only the requests are stubbed, by route, the pure path helpers stay real.
const { applyChanges, getDirectories } = vi.hoisted(() => ({
  applyChanges: vi.fn<(operations: ChangeOperation[]) => Promise<WorkspaceChanged>>(),
  getDirectories: vi.fn<(scope: string, signal?: AbortSignal) => Promise<DirectoryTreeResponse>>(),
}));

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
      : getDirectories(
          decodeURIComponent(url.split("/api/v1/directories/")[1] ?? ""),
          init?.signal ?? undefined,
        ),
}));

const treeWith = (filename: string): DirectoryTreeResponse => ({
  root: {
    type: "directory",
    name: "",
    path: "",
    children: [
      {
        type: "file",
        name: filename,
        path: filename,
        size_bytes: 1,
        modified_at: "2025-01-01T00:00:00Z",
      },
    ],
  },
  total_files: 1,
  total_directories: 1,
});

const changed = (scopes: string[]): WorkspaceChanged => ({
  type: "workspace-changed",
  id: crypto.randomUUID(),
  scopes,
  moves: [],
  deletes: [],
});

const client = createTestQueryClient();
let unsubscribe: () => void;

/** Show the trees of *scopes* with one mutation, as the documents panel would. */
function renderTrees<TVariables>(
  scopes: string[],
  mutation: UseMutationOptions<void, Error, TVariables>,
) {
  return renderHook(
    () => ({
      trees: scopes.map((scope) => useQuery(documentQueries.tree(scope)).data),
      mutation: useMutation(mutation),
    }),
    { wrapper: createQueryWrapper(client) },
  );
}

beforeEach(() => {
  client.clear();
  vi.clearAllMocks();
  vi.mocked(getDirectories).mockResolvedValue(treeWith("a.md"));
  vi.mocked(applyChanges).mockImplementation(() => Promise.resolve(changed(["~"])));
  unsubscribe = syncWorkspaceQueries(client);
});

afterEach(() => unsubscribe());

describe("documentQueries.tree", () => {
  it("reads once more for any number of changes during a read", async () => {
    const { result } = renderTrees(["~"], documentMutations.move());
    await waitFor(() => expect(result.current.trees[0]).toBeDefined());

    const read = Promise.withResolvers<DirectoryTreeResponse>();
    vi.mocked(getDirectories).mockClear().mockReturnValueOnce(read.promise);
    act(() => [1, 2, 3].forEach(() => publishWorkspaceChanged(changed(["~"]))));
    read.resolve(treeWith("a.md"));

    await waitFor(() => expect(getDirectories).toHaveBeenCalledTimes(2));
    await act(() => client.query(documentQueries.tree("~")));
    expect(getDirectories).toHaveBeenCalledTimes(2);
  });
});

describe("documentMutations", () => {
  it("shows the new directory before the tree refresh lands", async () => {
    const { result } = renderTrees(["~"], documentMutations.createDir());
    await waitFor(() => expect(result.current.trees[0]).toBeDefined());

    const read = Promise.withResolvers<DirectoryTreeResponse>();
    vi.mocked(getDirectories).mockReturnValueOnce(read.promise);
    act(() => result.current.mutation.mutate({ paths: ["~/reports"] }));

    // Presence, not position: the tree view sorts children as it renders them.
    await waitFor(() =>
      expect(result.current.trees[0]?.root.children?.map((c) => c.path)).toContain("reports"),
    );
    read.resolve(treeWith("a.md"));
    expect(applyChanges).toHaveBeenCalledExactlyOnceWith([{ kind: "mkdir", path: "~/reports" }]);
  });

  it("sends a cross-workspace move as one request and refreshes each scope the feed names once", async () => {
    const { result } = renderTrees(["~", "@team"], documentMutations.move());
    await waitFor(() => expect(result.current.trees.every(Boolean)).toBe(true));
    vi.mocked(getDirectories).mockClear();
    // The backend announces the commit on the feed before it answers with it.
    vi.mocked(applyChanges).mockImplementationOnce(() => {
      const commit = changed(["@team", "~"]);
      publishWorkspaceChanged(commit);

      return Promise.resolve(commit);
    });

    await act(() =>
      result.current.mutation.mutateAsync({
        paths: ["~/a.md", "~/docs"],
        destination: (source) => (source === "~/a.md" ? "@team/b.md" : "@team/docs"),
      }),
    );

    expect(applyChanges).toHaveBeenCalledExactlyOnceWith([
      { kind: "move", source: "~/a.md", destination: "@team/b.md" },
      { kind: "move", source: "~/docs", destination: "@team/docs" },
    ]);
    const scopes = vi.mocked(getDirectories).mock.calls.map(([scope]) => scope);
    expect(scopes.toSorted()).toEqual(["@team", "~"]);
  });

  it("refreshes the tree after a failed change", async () => {
    const { result } = renderTrees(["~"], documentMutations.remove());
    await waitFor(() => expect(result.current.trees[0]).toBeDefined());
    vi.mocked(getDirectories).mockClear();
    vi.mocked(applyChanges).mockRejectedValueOnce(new Error("Document not found: ~/b.md"));

    await act(() =>
      result.current.mutation
        .mutateAsync({ paths: ["~/a.md", "~/b.md"], dirs: new Set() })
        .catch(() => {}),
    );

    expect(applyChanges).toHaveBeenCalledExactlyOnceWith([
      { kind: "delete", path: "~/a.md", expect: "entry" },
      { kind: "delete", path: "~/b.md", expect: "entry" },
    ]);
    expect(getDirectories).toHaveBeenCalledExactlyOnceWith("~", expect.any(AbortSignal));
  });
});
