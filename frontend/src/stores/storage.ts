/**
 * Centralized localStorage utilities for all zustand persist stores.
 */

/** All localStorage keys used by the application. */
const STORAGE_KEYS = [
  'snipscout-settings',
  'snipscout-local-auth',
] as const;

/**
 * Remove all snipscout-related localStorage entries and reload the page.
 *
 * Used by the error boundary and the settings dialog as an escape hatch
 * when persisted state becomes corrupted.
 */
export function clearAllStorage(): void {
  for (const key of STORAGE_KEYS) {
    localStorage.removeItem(key);
  }
  window.location.reload();
}
