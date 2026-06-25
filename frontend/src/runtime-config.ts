import { z } from "zod";

import { API_BASE_URL } from "@/lib/health";

/**
 * Runtime configuration the SPA fetches from the backend (`GET /api/config`) at
 * startup — the single source of truth for its OIDC client. The backend derives
 * it from its own settings, so frontend and backend always agree and the same
 * prebuilt bundle runs against any OIDC provider with no rebuild.
 */

const schema = z.object({
  oidc: z.object({
    issuer_uri: z.string(),
    client_id: z.string(),
  }),
});

export type RuntimeConfig = z.infer<typeof schema>;

// Deliberately on plain fetch, not the readiness-gated authFetch: this is the
// pre-render OIDC bootstrap, so a missing backend must fail fast to the dev
// mock fallback rather than block rendering on a backend that may never come.
export async function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  const res = await fetch(`${API_BASE_URL}/api/config`);
  if (!res.ok) {
    throw new Error(`Failed to fetch /api/config: ${res.status}`);
  }
  return schema.parse(await res.json());
}
