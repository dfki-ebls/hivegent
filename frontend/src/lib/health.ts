/**
 * Backend origin and readiness probing. The foundational connection layer the
 * rest of the app builds on. Deliberately free of app-module imports (no auth,
 * no OIDC) so both the API client and the pre-render OIDC bootstrap can gate on
 * backend readiness without forming an import cycle.
 */

/** The same-origin URL of the API route *path*, which starts with a slash.
 *
 * Always same-origin, since the CSP allows `connect-src 'self'` and the OIDC
 * token substitution only applies to same-origin requests.
 */
export function apiUrl(path: string): string {
  return `/api/v1${path}`;
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
 * The probe runs once per app lifetime. The promise is cached and shared by
 * every caller, so startup fetches such as settings can gate on readiness
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
