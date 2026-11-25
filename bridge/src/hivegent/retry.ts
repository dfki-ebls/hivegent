/** Retry of the startup checks, so the bridge waits for an IdP or backend that is still starting. */

/** A response outside 2xx, whose status tells a transient failure from a permanent one. */
export class HttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** A request that never got a response, which `fetch` rejects with as a `TypeError`. */
export class NetworkError extends Error {}

/** `fetch`, whose network failures reject as a :class:`NetworkError`, so a `TypeError` of a bug stays one. */
export async function fetchRetryable(input: string | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (err) {
    if (err instanceof TypeError) {
      throw new NetworkError(err.message, { cause: err });
    }

    throw err;
  }
}

/**
 * Whether *err* may pass once the peer is up: a 5xx or 429, a network error,
 * or a timeout.
 */
export function isTransient(err: unknown): boolean {
  if (err instanceof HttpError) {
    return err.status >= 500 || err.status === 429;
  }

  return (
    err instanceof NetworkError || (err instanceof DOMException && err.name === "TimeoutError")
  );
}

const FIRST_DELAY_MS = 1_000;
const MAX_DELAY_MS = 30_000;
const DEADLINE_MS = 5 * 60_000;

/** Run *attempt* until it succeeds, backing off on transient failures for up to five minutes. */
export async function retryTransient<T>(what: string, attempt: () => Promise<T>): Promise<T> {
  const deadline = Date.now() + DEADLINE_MS;

  for (let delay = FIRST_DELAY_MS; ; delay = Math.min(delay * 2, MAX_DELAY_MS)) {
    try {
      return await attempt();
    } catch (err) {
      if (!isTransient(err) || Date.now() + delay > deadline) {
        throw err;
      }

      console.warn(`${what} failed, retrying in ${delay / 1000}s`, err);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
}
