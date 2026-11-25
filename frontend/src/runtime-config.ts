import { z } from "zod";

import { i18n } from "@/i18n";
import { apiUrl } from "@/lib/health";

/**
 * Runtime configuration the SPA fetches from the backend (`GET /api/v1/config`) at
 * startup, the single source of truth for its OIDC client and handbook link. The
 * backend derives it from its own settings, so frontend and backend always agree
 * and the same prebuilt bundle runs against any deployment with no rebuild.
 */

const schema = z.object({
  /** Null when `hivegent dev` runs the backend as the development user. */
  oidc: z
    .object({
      issuer_uri: z.string(),
      client_id: z.string(),
    })
    .nullable(),
  /** Null when no handbook is linked. */
  docs_url: z.string().nullable(),
});

type RuntimeConfig = z.infer<typeof schema>;

let loaded: RuntimeConfig | undefined;

// Deliberately on plain fetch, not the readiness-gated authFetch: this is the
// pre-render OIDC bootstrap, which waits for readiness itself.
export async function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  const res = await fetch(apiUrl("/config"));
  if (!res.ok) {
    throw new Error(i18n.t(($) => $.app.startup.configFailed, { status: res.status }));
  }

  loaded = schema.parse(await res.json());

  return loaded;
}

/** The configuration the bootstrap loaded, which every component below the bootstrap gate may read. */
export function runtimeConfig(): RuntimeConfig {
  if (!loaded) {
    throw new Error("The runtime configuration is read before the bootstrap loaded it.");
  }

  return loaded;
}
