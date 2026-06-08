/**
 * OIDC configuration using oidc-spa.
 *
 * Environment variables:
 * - VITE_OIDC_ISSUER_URI: The OIDC provider's URL
 * - VITE_OIDC_CLIENT_ID: The client ID for this application
 * - VITE_OIDC_USE_MOCK: Set to "true" to opt into mock mode (dev builds only)
 *
 * Production builds without VITE_OIDC_ISSUER_URI fail loudly rather than
 * silently falling back to mock mode, so a missing env var can't ship a
 * "logged in as Localhost User" page to real users.
 */

import { oidcSpa } from "oidc-spa/react-spa";
import { z } from "zod";

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

const mockExplicit = import.meta.env.VITE_OIDC_USE_MOCK === "true";
const issuerUri = import.meta.env.VITE_OIDC_ISSUER_URI;

if (mockExplicit && !import.meta.env.DEV) {
  throw new Error("OIDC mock mode is only allowed in development builds.");
}

if (!mockExplicit && !issuerUri && !import.meta.env.DEV) {
  throw new Error(
    "OIDC misconfigured: VITE_OIDC_ISSUER_URI must be set in production builds. " +
      "Mock mode is only available in development builds.",
  );
}

if (!mockExplicit && !issuerUri && import.meta.env.DEV) {
  console.warn("[oidc] VITE_OIDC_ISSUER_URI is not set; falling back to mock mode (dev only).");
}

const useMock = import.meta.env.DEV && (mockExplicit || !issuerUri);

void bootstrapOidc(
  useMock
    ? {
        implementation: "mock",
        isUserInitiallyLoggedIn: true,
      }
    : {
        implementation: "real",
        issuerUri,
        clientId: import.meta.env.VITE_OIDC_CLIENT_ID,
      },
);
