import { afterEach, vi } from "vitest";

import { clearAuthTokenProvider } from "@/lib/api";

afterEach(() => {
  clearAuthTokenProvider();
  vi.restoreAllMocks();
});
