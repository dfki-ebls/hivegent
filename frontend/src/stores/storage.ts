/**
 * Centralized localStorage utilities for all zustand persist stores.
 */

import { deleteCryptoDatabase } from "../lib/crypto";

/** All localStorage keys used by the application. */
const STORAGE_KEYS = ["hivegent-settings", "hivegent-local-auth"] as const;

/**
 * Remove all hivegent-related localStorage entries and reload the page.
 *
 * Also deletes the IndexedDB database holding the encryption key.
 * Used by the error boundary and the settings dialog as an escape hatch
 * when persisted state becomes corrupted.
 */
export function clearAllStorage(): void {
  for (const key of STORAGE_KEYS) {
    localStorage.removeItem(key);
  }
  deleteCryptoDatabase();
  window.location.reload();
}
