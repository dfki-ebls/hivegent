/**
 * OIDC configuration for authentication.
 *
 * Environment variables:
 * - VITE_OIDC_AUTHORITY: The OIDC provider's URL (e.g., https://auth.example.com/realms/snipscout)
 * - VITE_OIDC_CLIENT_ID: The client ID for this application
 */

import type { UserManagerSettings } from 'oidc-client-ts';

export const oidcConfig: UserManagerSettings = {
  authority: import.meta.env.VITE_OIDC_AUTHORITY ?? '',
  client_id: import.meta.env.VITE_OIDC_CLIENT_ID ?? '',
  redirect_uri: `${window.location.origin}/auth/callback`,
  post_logout_redirect_uri: window.location.origin,
  scope: 'openid profile email',
  response_type: 'code',
  automaticSilentRenew: true,
  // Disable silent renew iframe to avoid issues
  silentRequestTimeoutInSeconds: 30,
};

/**
 * Check if OIDC is configured.
 */
export function isOidcConfigured(): boolean {
  return Boolean(oidcConfig.authority && oidcConfig.client_id);
}
