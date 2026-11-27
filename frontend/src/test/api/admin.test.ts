import { expect, it, onTestFinished, vi } from "vitest";

import { adminQueries, syncSourceQueries } from "@/api/admin";
import type { subscribeJobs } from "@/api/jobs";
import { startFeed } from "@/test/feed";
import { createTestQueryClient } from "@/test/render";

vi.mock("@/api/jobs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/jobs")>()),
  subscribeJobs: vi.fn<typeof subscribeJobs>(),
}));

it("refetches the sources when the feed announces a change", async () => {
  const client = createTestQueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  onTestFinished(syncSourceQueries(client));
  const feed = await startFeed();

  feed.onSourcesChanged();

  expect(invalidate).toHaveBeenCalledWith({ queryKey: adminQueries.sources().queryKey });
});
