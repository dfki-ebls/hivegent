import { vi } from "vitest";

import { type FeedHandlers, subscribeJobs } from "@/api/jobs";
import { useJobsStore } from "@/stores/jobs-store";

/**
 * Open the job feed on a connection that never ends and return the handlers it serves.
 * The test file mocks `@/api/jobs` with a stubbed `subscribeJobs`, and runs on fake timers.
 */
export async function startFeed(): Promise<FeedHandlers> {
  let served: FeedHandlers | undefined;
  vi.mocked(subscribeJobs).mockImplementation((handlers) => {
    served = handlers;

    return new Promise(() => {});
  });
  useJobsStore.setState({ started: false });
  useJobsStore.getState().start();
  await vi.advanceTimersByTimeAsync(0);

  if (!served) throw new Error("The feed never connected");

  return served;
}
