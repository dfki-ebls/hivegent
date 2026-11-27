import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { uploadDocument } from "@/api/documents";
import { HttpError } from "@/api/client";
import type { JobView } from "@/api/jobs";
import { useUploadQueue } from "@/stores/upload-queue-store";

vi.mock("@/api/documents", () => ({
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

const job = (id: string): JobView => ({
  id,
  kind: "document.upload",
  title: id,
  status: "queued",
  stage: null,
  progress: null,
  error: null,
  created_at: 0,
  updated_at: 0,
});

const queue = () => useUploadQueue.getState();
const items = () => Object.values(queue().items);
const enqueue = (...names: string[]) =>
  queue().enqueueFiles(
    "~",
    names.map((name) => new File(["x"], name)),
  );
const settle = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => {
  pending = [];
  useUploadQueue.setState({ items: {} });
  vi.mocked(uploadDocument).mockImplementation(
    (_path, _file, opts) =>
      new Promise<JobView>((resolve, reject) => {
        pending.push({ resolve, reject, signal: opts?.signal });
        opts?.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        );
      }),
  );
});

// An upload left running would hold one of the queue's slots for the next test.
afterEach(async () => {
  items().forEach((item) => queue().cancel(item.id));
  await settle();
});

it("appends new files without aborting uploads in flight and drops them once handed to a job", async () => {
  enqueue("a.pdf");
  enqueue("b.pdf");

  expect(pending).toHaveLength(2);
  expect(pending[0]?.signal?.aborted).toBe(false);

  pending[0]?.resolve(job("j1"));
  pending[1]?.resolve(job("j2"));
  await settle();

  expect(items()).toHaveLength(0);
});

it("skips re-dropping a file already in the queue", () => {
  enqueue("dup.pdf", "dup.pdf");
  enqueue("dup.pdf");

  expect(items()).toHaveLength(1);
  expect(uploadDocument).toHaveBeenCalledTimes(1);
});

it("marks a failed transfer as retryable and uploads it again on retry", async () => {
  enqueue("f.pdf");
  pending[0]?.reject(new Error("network down"));
  await settle();

  expect(items()[0]).toMatchObject({ status: "failed", error: "network down" });

  queue().retry(items()[0]?.id ?? "");

  expect(pending).toHaveLength(2);
});

it("uploads a rate-limited transfer again once its Retry-After passed", async () => {
  enqueue("r.pdf");
  pending[0]?.reject(new HttpError("slow down", 429, 3));
  await vi.advanceTimersByTimeAsync(2999);

  expect(pending).toHaveLength(1);

  await vi.advanceTimersByTimeAsync(1);

  expect(pending).toHaveLength(2);
  expect(items()[0]?.status).toBe("uploading");
});
