/**
 * OIDC configuration using oidc-spa.
 *
 * The issuer and client id come from the backend at runtime via
 * {@link fetchRuntimeConfig} (`GET /api/v1/config`), the single source of truth,
 * in dev and prod alike. `hivegent serve` always serves an OIDC client, and
 * `hivegent dev` none, as it acts as its development user, so
 * the config alone decides real vs mock with no frontend-side environment branch
 * to drift out of sync.
 *
 * {@link initOidc} runs the whole sequence once, driven by BootstrapGate.
 */

import { oidcSpa } from "oidc-spa/react-spa";
import { z } from "zod";

import { waitForBackendReady } from "@/lib/health";
import { fetchRuntimeConfig } from "@/runtime-config";

const { bootstrapOidc, useOidc, getOidc, enforceLogin, OidcInitializationGate } = oidcSpa
  .withExpectedDecodedIdTokenShape({
    decodedIdTokenSchema: z.object({
      sub: z.string(),
      name: z.string().optional(),
      given_name: z.string().optional(),
      family_name: z.string().optional(),
      preferred_username: z.string().optional(),
      email: z.string().optional(),
    }),
    decodedIdToken_mock: {
      sub: "localhost",
      given_name: "Localhost",
      family_name: "User",
      email: "dev@localhost",
    },
  })
  .createUtils();

export { useOidc, getOidc, enforceLogin, OidcInitializationGate };

let bootstrapPromise: Promise<void> | null = null;

/**
 * Wait for the backend, load its runtime config, and bootstrap OIDC, the full
 * startup sequence the app gates on (see the BootstrapGate component). The
 * promise is cached so React's StrictMode double-mount, and any other caller,
 * reuses the one in-flight bootstrap rather than starting a second.
 */
export function initOidc(): Promise<void> {
  bootstrapPromise ??= runBootstrap();

  return bootstrapPromise;
}

async function runBootstrap(): Promise<void> {
  // The backend (every route, including /api/v1/config) answers 503 while it boots
  // behind the reverse proxy, so wait for readiness before fetching the config
  // rather than letting it throw; BootstrapGate keeps the connection spinner on
  // screen for the whole wait.
  await waitForBackendReady();
  const { oidc } = await fetchRuntimeConfig();

  // An OIDC client means real sign-in, none the backend's development user,
  // which is mocked, so the same path serves dev and prod.
  await bootstrapOidc(
    oidc
      ? {
          implementation: "real",
          issuerUri: oidc.issuer_uri,
          clientId: oidc.client_id,
        }
      : {
          implementation: "mock",
          isUserInitiallyLoggedIn: true,
        },
  );
}
