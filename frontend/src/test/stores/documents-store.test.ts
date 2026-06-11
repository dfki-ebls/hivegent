import { beforeEach, describe, expect, it, vi } from "vitest";

import type { getDirectories as getDirectoriesFn } from "@/lib/api";

// Only the refresh read is exercised; the remaining store imports just
// need to exist for the module to load (the factory is hoisted, so no helpers).
vi.mock("@/lib/api", () => ({
  bulkDeleteStream: vi.fn<() => void>(),
  bulkRechunkStream: vi.fn<() => void>(),
  bulkReconvertStream: vi.fn<() => void>(),
  canonicalPath: (scope: string, local: string) => `${scope}/${local}`,
  createDirectory: vi.fn<() => void>(),
  deleteDirectory: vi.fn<() => void>(),
  deleteDocument: vi.fn<() => void>(),
  getDirectories: vi.fn<typeof getDirectoriesFn>(),
  moveDirectory: vi.fn<() => void>(),
  moveDocument: vi.fn<() => void>(),
  rechunkDocumentStream: vi.fn<() => void>(),
  reconvertDocumentStream: vi.fn<() => void>(),
  uploadCollectionStream: vi.fn<() => void>(),
  uploadDocumentStream: vi.fn<() => void>(),
}));

import { getDirectories } from "@/lib/api";
import type { DirectoryTreeResponse } from "@/lib/types";
import { useDocumentsStore } from "@/stores/documents-store";

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("useDocumentsStore refresh", () => {
  beforeEach(() => {
    useDocumentsStore.setState({ byScope: {} });
    vi.clearAllMocks();
  });

  it("derives the document list from the tree", async () => {
    vi.mocked(getDirectories).mockResolvedValueOnce(treeWith("notes.md"));

    await useDocumentsStore.getState().refresh("~");

    const state = useDocumentsStore.getState().byScope["~"];
    expect(state.documents.map((d) => d.filename)).toEqual(["notes.md"]);
    expect(state.directoryTree?.total_files).toBe(1);
  });

  it("ignores an older refresh resolving after a newer one", async () => {
    const older = deferred<DirectoryTreeResponse>();
    const newer = deferred<DirectoryTreeResponse>();
    vi.mocked(getDirectories)
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);

    const first = useDocumentsStore.getState().refresh("~");
    const second = useDocumentsStore.getState().refresh("~");

    newer.resolve(treeWith("new.md"));
    await second;
    older.resolve(treeWith("old.md"));
    await first;

    const state = useDocumentsStore.getState().byScope["~"];
    expect(state.documents.map((d) => d.filename)).toEqual(["new.md"]);
  });
});
