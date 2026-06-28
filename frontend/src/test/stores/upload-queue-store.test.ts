import { beforeEach, describe, expect, it, vi } from "vitest";

import type { JobView } from "@/lib/types";
import { uploadDocument } from "@/lib/api";
import { useUploadQueue } from "@/stores/upload-queue-store";

vi.mock("@/lib/api", () => ({
  canonicalPath: (scope: string, name: string) => `${scope}/${name}`,
  uploadDocument: vi.fn<typeof uploadDocument>(),
  uploadCollection: vi.fn<() => void>(),
}));

vi.mock("@/stores/jobs-store", () => ({
  useJobsStore: { getState: () => ({ upsert: vi.fn<() => void>() }) },
}));

interface PendingUpload {
  resolve: (job: JobView) => void;
  reject: (err: unknown) => void;
  signal?: AbortSignal;
}

let pending: PendingUpload[] = [];

const makeJob = (id: string): JobView => ({
  id,
  kind: "document.upload",
  title: id,
  scope: "~",
  status: "queued",
  stage: null,
  progress: null,
  error: null,
  created_at: 0,
  updated_at: 0,
});

const items = () => Object.values(useUploadQueue.getState().items);

describe("useUploadQueue", () => {
  beforeEach(() => {
    pending = [];
    vi.clearAllMocks();
    useUploadQueue.setState({ items: {} });
    vi.mocked(uploadDocument).mockImplementation((_path, _file, opts) => {
      return new Promise<JobView>((resolve, reject) => {
        pending.push({ resolve, reject, signal: opts?.signal });
        opts?.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        );
      });
    });
  });

  it("appends new files without aborting uploads already in flight", async () => {
    useUploadQueue.getState().enqueueFiles("~", [new File(["a"], "a.pdf")], {});
    await vi.waitFor(() => expect(pending).toHaveLength(1));

    // Dropping more files while the first is still uploading must not cancel it.
    useUploadQueue.getState().enqueueFiles("~", [new File(["b"], "b.pdf")], {});
    await vi.waitFor(() => expect(pending).toHaveLength(2));
    expect(pending[0].signal?.aborted).toBe(false);

    // Both hand off to a job and leave the queue.
    pending[0].resolve(makeJob("j1"));
    pending[1].resolve(makeJob("j2"));
    await vi.waitFor(() => expect(items()).toHaveLength(0));
    expect(uploadDocument).toHaveBeenCalledTimes(2);
  });

  it("skips re-dropping a file already in the queue", async () => {
    useUploadQueue.getState().enqueueFiles("~", [new File(["x"], "dup.pdf")], {});
    await vi.waitFor(() => expect(pending).toHaveLength(1));

    useUploadQueue.getState().enqueueFiles("~", [new File(["x"], "dup.pdf")], {});

    expect(items()).toHaveLength(1);
    expect(uploadDocument).toHaveBeenCalledTimes(1);
  });

  it("marks a failed transfer as retryable and re-queues it on retry", async () => {
    useUploadQueue.getState().enqueueFiles("~", [new File(["x"], "f.pdf")], {});
    await vi.waitFor(() => expect(pending).toHaveLength(1));
    pending[0].reject(new Error("network down"));

    let id = "";
    await vi.waitFor(() => {
      const item = items()[0];
      expect(item.status).toBe("failed");
      expect(item.error).toBe("network down");
      id = item.id;
    });

    useUploadQueue.getState().retry(id);
    await vi.waitFor(() => expect(pending).toHaveLength(2));
  });
});
