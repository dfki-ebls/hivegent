import { expect, it, vi } from "vitest";

import { adminQueries, syncSourceQueries } from "@/api/admin";
import { type FeedHandlers, subscribeJobs } from "@/api/jobs";
import { useJobsStore } from "@/stores/jobs-store";
import { createTestQueryClient } from "@/test/render";

vi.mock("@/api/jobs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/jobs")>()),
  subscribeJobs: vi.fn<typeof subscribeJobs>(),
}));

it("refetches the sources when the feed announces a change", async () => {
  const feed = Promise.withResolvers<FeedHandlers>();
  vi.mocked(subscribeJobs).mockImplementation(async (handlers) => {
    feed.resolve(handlers);
    await new Promise<never>(() => {});
  });
  const client = createTestQueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  syncSourceQueries(client);
  useJobsStore.getState().start();

  const { onSourcesChanged } = await feed.promise;
  onSourcesChanged();

  expect(invalidate).toHaveBeenCalledWith({ queryKey: adminQueries.sources().queryKey });
});
