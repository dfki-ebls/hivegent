/**
 * OIDC configuration using oidc-spa.
 *
 * The issuer and client id come from the backend at runtime via
 * {@link fetchRuntimeConfig} (`GET /api/config`) — the single source of truth.
 * Mock mode is used in dev builds when no issuer is configured (or the backend
 * is unreachable); production builds fail loudly on a missing issuer rather than
 * shipping a "logged in as Localhost User" page to real users.
 *
 * Call {@link initOidc} once before rendering (see `main.tsx`).
 */

import { oidcSpa } from "oidc-spa/react-spa";
import { z } from "zod";

import { fetchRuntimeConfig } from "@/runtime-config";

export const { bootstrapOidc, useOidc, getOidc, enforceLogin, OidcInitializationGate } = oidcSpa
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

export async function initOidc(): Promise<void> {
  let issuerUri = "";
  let clientId = "";

  try {
    const config = await fetchRuntimeConfig();
    issuerUri = config.oidc.issuer_uri;
    clientId = config.oidc.client_id;
  } catch (error) {
    // An unreachable backend is a dev convenience (mock); in production it is a
    // hard failure rather than a silent fallback.
    if (!import.meta.env.DEV) throw error;
    console.warn("[oidc] could not load /api/config; falling back to mock mode (dev only).", error);
  }

  if (!issuerUri && !import.meta.env.DEV) {
    throw new Error(
      "OIDC misconfigured: the backend returned no issuer. Set HIVEGENT_AUTH__ISSUER.",
    );
  }

  // No issuer in a dev build → mock; production has already thrown above.
  const useMock = import.meta.env.DEV && !issuerUri;

  await bootstrapOidc(
    useMock
      ? {
          implementation: "mock",
          isUserInitiallyLoggedIn: true,
        }
      : {
          implementation: "real",
          issuerUri,
          clientId,
        },
  );
}
