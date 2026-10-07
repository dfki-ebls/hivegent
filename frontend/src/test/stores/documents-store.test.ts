import { beforeEach, describe, expect, it, vi } from "vitest";

import type { applyChanges as applyChangesFn, getDirectories as getDirectoriesFn } from "@/lib/api";

const jobEvents = vi.hoisted(() => ({ feedReady: undefined as (() => void) | undefined }));

// Only the requests are stubbed, the pure path helpers stay real.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  applyChanges: vi.fn<typeof applyChangesFn>(),
  getDirectories: vi.fn<typeof getDirectoriesFn>(),
}));

// Only the feed-ready hook is intercepted, to fire the handshake by hand; the
// rest of the store stays real so this mock never has to track its exports.
vi.mock("@/stores/jobs-store", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/stores/jobs-store")>()),
  onFeedReady: (listener: () => void) => {
    jobEvents.feedReady = listener;
  },
}));

import { applyChanges, getDirectories } from "@/lib/api";
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

  it("reconciles loaded scopes after the feed connects", async () => {
    vi.mocked(getDirectories).mockResolvedValue(treeWith("notes.md"));
    await useDocumentsStore.getState().refresh("~");
    vi.clearAllMocks();

    jobEvents.feedReady?.();

    expect(getDirectories).toHaveBeenCalledExactlyOnceWith("~");
  });
});

describe("useDocumentsStore changes", () => {
  beforeEach(() => {
    useDocumentsStore.setState({ byScope: {} });
    vi.clearAllMocks();
    vi.mocked(getDirectories).mockResolvedValue(treeWith("a.md"));
    vi.mocked(applyChanges).mockResolvedValue();
  });

  it("shows the new directory before the tree refresh lands", async () => {
    await useDocumentsStore.getState().refresh("~");

    const pending = deferred<DirectoryTreeResponse>();
    vi.mocked(getDirectories).mockReturnValueOnce(pending.promise);
    const done = useDocumentsStore.getState().createDir("~", "reports");

    // Presence, not position: the tree view sorts children as it renders them.
    await vi.waitFor(() => {
      const children = useDocumentsStore.getState().byScope["~"].directoryTree?.root.children;
      expect(children?.map((c) => c.path)).toContain("reports");
    });

    pending.resolve(treeWith("a.md"));
    await done;

    expect(applyChanges).toHaveBeenCalledExactlyOnceWith([{ kind: "mkdir", path: "~/reports" }]);
  });

  it("sends a cross-workspace move as one request and refreshes both scopes", async () => {
    await useDocumentsStore.getState().move("~", "@team", [
      { source: "a.md", destination: "b.md" },
      { source: "docs", destination: "docs" },
    ]);

    expect(applyChanges).toHaveBeenCalledExactlyOnceWith([
      { kind: "move", source: "~/a.md", destination: "@team/b.md" },
      { kind: "move", source: "~/docs", destination: "@team/docs" },
    ]);
    const scopes = vi.mocked(getDirectories).mock.calls.map((call) => call[0]);
    expect(scopes).toEqual(expect.arrayContaining(["~", "@team"]));
    expect(useDocumentsStore.getState().byScope["~"].error).toBeNull();
  });

  it("shows the request's single error and clears every spinner", async () => {
    vi.mocked(applyChanges).mockRejectedValueOnce(new Error("Document not found: ~/b.md"));

    await useDocumentsStore.getState().remove("~", ["a.md", "b.md"], "entry");

    expect(applyChanges).toHaveBeenCalledExactlyOnceWith([
      { kind: "delete", path: "~/a.md", expect: "entry" },
      { kind: "delete", path: "~/b.md", expect: "entry" },
    ]);
    const state = useDocumentsStore.getState().byScope["~"];
    expect(state.error).toBe("Document not found: ~/b.md");
    expect(state.mutatingPaths.size).toBe(0);
    expect(getDirectories).toHaveBeenCalledWith("~");
  });
});
