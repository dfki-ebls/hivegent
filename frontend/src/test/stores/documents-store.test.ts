import { beforeEach, describe, expect, it, vi } from "vitest";

import type { getDirectories as getDirectoriesFn } from "@/lib/api";

// Only the refresh read is exercised; the remaining store imports just
// need to exist for the module to load (the factory is hoisted, so no helpers).
vi.mock("@/lib/api", () => ({
  bulkDelete: vi.fn<() => void>(),
  bulkMove: vi.fn<() => void>(),
  bulkRechunk: vi.fn<() => void>(),
  bulkReconvert: vi.fn<() => void>(),
  cancelJob: vi.fn<() => void>(),
  canonicalPath: (scope: string, local: string) => `${scope}/${local}`,
  createDirectory: vi.fn<() => void>(),
  deleteDirectory: vi.fn<() => void>(),
  deleteDocument: vi.fn<() => void>(),
  getDirectories: vi.fn<typeof getDirectoriesFn>(),
  moveDirectory: vi.fn<() => void>(),
  moveDocument: vi.fn<() => void>(),
  rechunkDocument: vi.fn<() => void>(),
  reconvertDocument: vi.fn<() => void>(),
  subscribeJobs: vi.fn<() => void>(),
  uploadCollection: vi.fn<() => void>(),
  uploadDocument: vi.fn<() => void>(),
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

  it("collapses concurrent refreshes into one read and a shared follow-up", async () => {
    const inFlight = deferred<DirectoryTreeResponse>();
    vi.mocked(getDirectories)
      .mockReturnValueOnce(inFlight.promise)
      .mockResolvedValue(treeWith("new.md"));

    const first = useDocumentsStore.getState().refresh("~");
    const second = useDocumentsStore.getState().refresh("~");
    const third = useDocumentsStore.getState().refresh("~");

    inFlight.resolve(treeWith("old.md"));
    await Promise.all([first, second, third]);

    // Three callers, two walks: the one already running plus a single follow-up
    // that both later callers wait on. Each read is the whole tree, so the last
    // one wins outright.
    expect(vi.mocked(getDirectories)).toHaveBeenCalledTimes(2);
    const state = useDocumentsStore.getState().byScope["~"];
    expect(state.documents.map((d) => d.filename)).toEqual(["new.md"]);
  });
});

describe("useDocumentsStore createDir", () => {
  beforeEach(() => {
    useDocumentsStore.setState({ byScope: {} });
    vi.clearAllMocks();
  });

  it("shows the new directory before the tree refresh lands", async () => {
    vi.mocked(getDirectories).mockResolvedValueOnce(treeWith("notes.md"));
    await useDocumentsStore.getState().refresh("~");

    const pending = deferred<DirectoryTreeResponse>();
    vi.mocked(getDirectories).mockReturnValueOnce(pending.promise);
    const done = useDocumentsStore.getState().createDir("~", "reports");

    // Presence, not position: the tree view sorts children as it renders them.
    await vi.waitFor(() => {
      const children = useDocumentsStore.getState().byScope["~"].directoryTree?.root.children;
      expect(children?.map((c) => c.path)).toContain("reports");
    });

    pending.resolve(treeWith("notes.md"));
    await done;
  });
});

describe("useDocumentsStore move", () => {
  beforeEach(() => {
    useDocumentsStore.setState({ byScope: {} });
    vi.clearAllMocks();
  });

  it("refreshes both the source and destination scope on a cross-workspace move", async () => {
    vi.mocked(getDirectories).mockResolvedValue(treeWith("notes.md"));

    await useDocumentsStore.getState().move("~", "notes.md", "@team", "notes.md");

    const scopes = vi.mocked(getDirectories).mock.calls.map((call) => call[0]);
    expect(scopes).toContain("~");
    expect(scopes).toContain("@team");
  });
});
