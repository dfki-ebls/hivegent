/**
 * Clear everything the app keeps in the browser's storage and reload the page.
 *
 * Used by the error boundary and the settings dialog as an escape hatch
 * when stored state becomes corrupted.  The origin is the app's own, so
 * every entry is one of its stores or of its sign-in.
 */
export function clearAllStorage(): void {
  localStorage.clear();
  sessionStorage.clear();
  window.location.reload();
}
