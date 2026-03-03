/**
 * OIDC configuration using oidc-spa.
 *
 * Environment variables:
 * - VITE_OIDC_ISSUER_URI: The OIDC provider's URL
 * - VITE_OIDC_CLIENT_ID: The client ID for this application
 * - VITE_OIDC_USE_MOCK: Set to "true" to force mock mode
 */

import { oidcSpa } from "oidc-spa/react-spa";
import { z } from "zod";

export const { bootstrapOidc, useOidc, getOidc, enforceLogin, OidcInitializationGate } = oidcSpa
  .withExpectedDecodedIdTokenShape({
    decodedIdTokenSchema: z.object({
      sub: z.string(),
      name: z.string().optional(),
      preferred_username: z.string().optional(),
      email: z.string().optional(),
    }),
    decodedIdToken_mock: {
      sub: "localhost",
      name: "Localhost User",
      email: "dev@localhost",
    },
  })
  .createUtils();

const useMock =
  import.meta.env.VITE_OIDC_USE_MOCK === "true" || !import.meta.env.VITE_OIDC_ISSUER_URI;

bootstrapOidc(
  useMock
    ? {
        implementation: "mock",
        isUserInitiallyLoggedIn: true,
      }
    : {
        implementation: "real",
        issuerUri: import.meta.env.VITE_OIDC_ISSUER_URI,
        clientId: import.meta.env.VITE_OIDC_CLIENT_ID,
      },
);
