/**
 * Backend origin and readiness probing — the foundational connection layer the
 * rest of the app builds on. Deliberately free of app-module imports (no auth,
 * no OIDC) so both the API client and the pre-render OIDC bootstrap can gate on
 * backend readiness without forming an import cycle.
 */

const API_BASE_URL = import.meta.env.VITE_API_URL ?? "";

/** The URL of the API route *path*, which starts with a slash. */
export function apiUrl(path: string): string {
  return `${API_BASE_URL}/api/v1${path}`;
}

/** Probe the public readiness endpoint, aborting after `timeoutMs` so
 * polling callers don't stack up hung connections. */
async function checkHealth(timeoutMs = 3000): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(apiUrl("/health"), {
      signal: controller.signal,
    });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

const READY_POLL_INTERVAL_MS = 1000;

let readyProbe: Promise<void> | null = null;

/**
 * Resolve once the backend reports healthy, polling `/api/v1/health` until then.
 *
 * The probe runs once per app lifetime — the promise is cached and shared by
 * every caller — so startup fetches such as settings can gate on readiness
 * without each racing the backend with their own retry loop. The health route
 * is exempt from the maintenance gate, so this still resolves during
 * maintenance and lets gated callers observe their own 503.
 */
export function waitForBackendReady(): Promise<void> {
  readyProbe ??= (async () => {
    while (!(await checkHealth())) {
      await new Promise((resolve) => setTimeout(resolve, READY_POLL_INTERVAL_MS));
    }
  })();

  return readyProbe;
}
