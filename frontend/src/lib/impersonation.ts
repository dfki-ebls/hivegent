/**
 * Admin impersonation state.
 *
 * Kept in sessionStorage so it is scoped to a single tab and survives the
 * full-page reloads that bracket an impersonation session. While active,
 * every API request carries the `X-Impersonate-User` header (attached in
 * `getAuthHeaders`) and the backend resolves the request identity to the
 * target user — the admin's own OIDC token keeps authenticating the call.
 */

const STORAGE_KEY = "hivegent-impersonation";

export const IMPERSONATE_HEADER = "X-Impersonate-User";

/** The user id currently being impersonated in this tab, if any. */
export function getImpersonation(): string | null {
  return sessionStorage.getItem(STORAGE_KEY);
}

/** Start impersonating and reload so every store rebuilds as the target. */
export function startImpersonation(userId: string): void {
  sessionStorage.setItem(STORAGE_KEY, userId);
  window.location.assign("/");
}

/** Stop impersonating and reload to restore the admin's own session. */
export function stopImpersonation(): void {
  sessionStorage.removeItem(STORAGE_KEY);
  window.location.assign("/");
}
