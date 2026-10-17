import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";

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
