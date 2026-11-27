import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";

import { createQueryClient } from "@/lib/query-client";

/** A fresh app query cache that fails fast, so no test sees another's server state. */
export function createTestQueryClient(): QueryClient {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });

  return client;
}

/** A provider of *client*, a fresh one by default. */
export function createQueryWrapper(client = createTestQueryClient()) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

/** Render *ui* inside {@link createQueryWrapper}. */
export function renderWithQueries(ui: ReactNode) {
  return render(ui, { wrapper: createQueryWrapper() });
}

/**
 * Advance the fake clock by *ms*, which runs every due timer and settles every promise
 * that resolves without outside input, inside `act` so React shows the result.
 */
export const settle = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));

/** One animation frame of the fake clock, which Base UI waits for before it unmounts a closed popup. */
export const FRAME_MS = 16;
