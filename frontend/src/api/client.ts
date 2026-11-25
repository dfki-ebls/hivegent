import { z } from "zod";

import { i18n, LANGUAGE } from "@/i18n";
import type { app } from "@/i18n/locales/en/app";
import { waitForBackendReady } from "@/lib/health";
import { getOidc } from "@/oidc";

/** The auth headers of every request. */
async function getAuthHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {
    // The backend answers in this language, prompts and tool schemas included.
    "Accept-Language": LANGUAGE,
  };
  const oidc = await getOidc();

  if (oidc.isUserLoggedIn) {
    headers.Authorization = `Bearer ${await oidc.getAccessToken()}`;
  }

  return headers;
}

/** An authenticated fetch, which waits for the backend to be ready first. */
async function authFetch(url: RequestInfo | URL, options: RequestInit = {}): Promise<Response> {
  // Startup fetches never race a backend that is still booting. The probe runs
  // once per app lifetime, so this is a no-op once ready.
  await waitForBackendReady();

  const headers = new Headers(options.headers);

  for (const [key, value] of Object.entries(await getAuthHeaders())) {
    headers.set(key, value);
  }

  // Responses are per account, so none may come from or go into the HTTP cache.
  return fetch(url, { cache: "no-store", ...options, headers });
}

/** Client-side fallback for a failed request, resolved in the interface language when the error is raised. */
type ApiError = keyof typeof app.api.errors;

/**
 * Human explanation for the edge statuses that reach the client without a problem
 * body, since the reverse proxy rather than the API produced them.
 * Null when a status carries no more meaning than itself.
 */
function statusExplanation(status: number): string | null {
  switch (status) {
    case 413:
      return i18n.t(($) => $.app.api.status.tooLarge);
    case 429:
      return i18n.t(($) => $.app.api.status.tooManyRequests);
    case 502:
    case 503:
    case 504:
      return i18n.t(($) => $.app.api.status.unavailable);
    default:
      return null;
  }
}

/** Message for a failure without a problem `detail`, suffixed with its status for diagnostics. */
function fallbackMessage(error: ApiError, status: number): string {
  return `${statusExplanation(status) ?? i18n.t(($) => $.app.api.errors[error])} (HTTP ${status})`;
}

/** A request the backend answered with an error status, and the seconds its `Retry-After` asks to wait. */
export class HttpError extends Error {
  constructor(
    message: string | undefined,
    readonly status: number,
    readonly retryAfter: number | null = null,
  ) {
    super(message);
  }
}

/** The seconds a `Retry-After` *value* asks to wait, given as seconds or as an HTTP date. */
function parseRetryAfter(value: string | null): number | null {
  if (value === null || value.trim() === "") return null;

  const seconds = Number(value);
  const wait = Number.isFinite(seconds) ? seconds : (Date.parse(value) - Date.now()) / 1000;

  return Number.isNaN(wait) ? null : Math.max(0, wait);
}

/** Thrown when the backend rejects a request through the maintenance gate. */
export class MaintenanceError extends HttpError {}

/** Mirrors `MAINTENANCE_PROBLEM` in `backend/src/hivegent/server/maintenance.py`. */
const MAINTENANCE_PROBLEM = "tag:dfki.de,2026:hivegent/maintenance";

/** The RFC 9457 problem every API error answers with, as far as the client reads it. */
const ProblemSchema = z.object({ type: z.string().optional(), detail: z.string().optional() });

/** The error a failed *res* stands for. */
async function responseError(res: Response, error: ApiError): Promise<HttpError> {
  const problem = ProblemSchema.safeParse(await res.json().catch(() => null)).data;

  const retryAfter = parseRetryAfter(res.headers.get("Retry-After"));

  if (problem?.type === MAINTENANCE_PROBLEM) {
    return new MaintenanceError(problem.detail, res.status, retryAfter);
  }

  return new HttpError(
    problem?.detail || fallbackMessage(error, res.status),
    res.status,
    retryAfter,
  );
}

/** An authenticated request that throws the backend's error for a failure status. */
export async function checkedResponse(
  url: RequestInfo | URL,
  error: ApiError,
  options?: RequestInit,
): Promise<Response> {
  const response = await authFetch(url, options);

  if (!response.ok) throw await responseError(response, error);

  return response;
}

/** Wait *seconds*, rejecting with the abort reason once *signal* aborts. */
const sleep = (seconds: number, signal?: AbortSignal | null) =>
  new Promise<void>((resolve, reject) => {
    signal?.throwIfAborted();

    const timer = setTimeout(resolve, seconds * 1000);

    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(signal.reason);
      },
      { once: true },
    );
  });

/** Run *request* again after every rate limit, as long as its `Retry-After` asks, until *signal* aborts. */
export async function honoringRateLimits<T>(
  request: () => Promise<T>,
  signal?: AbortSignal | null,
): Promise<T> {
  for (;;) {
    try {
      return await request();
    } catch (err) {
      if (!(err instanceof HttpError && err.status === 429 && err.retryAfter !== null)) throw err;

      await sleep(err.retryAfter, signal);
    }
  }
}

/**
 * The fetch of a chat transport, which maps errors like every other request,
 * and waits out a rate limit, which refuses a turn before it starts.
 */
export const chatFetch: typeof fetch = (input, init) =>
  honoringRateLimits(() => checkedResponse(input, "chat", init), init?.signal);

export async function requestJson<T>(
  url: string,
  error: ApiError,
  schema: z.ZodType<T>,
  options?: RequestInit,
): Promise<T> {
  const response = await checkedResponse(url, error, options);

  return schema.parse(await response.json());
}

export async function requestVoid(
  url: string,
  error: ApiError,
  options?: RequestInit,
): Promise<void> {
  await checkedResponse(url, error, options);
}

export async function requestText(url: string, error: ApiError): Promise<string> {
  return (await checkedResponse(url, error)).text();
}

export async function requestBlob(
  url: string,
  error: ApiError,
  options?: RequestInit,
): Promise<Blob> {
  return (await checkedResponse(url, error, options)).blob();
}

export function jsonRequest(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body: unknown,
): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/**
 * Encode a file path for use in URLs, segment by segment, so `/` separators are preserved.
 * An absolute path such as `/tmp/...` keeps its leading slash encoded, so no proxy merges it
 * into the route's own slash.
 * A `.` or `..` segment is refused, since URL resolution would fold it, even percent-encoded,
 * into a request for another route.
 */
export function encodeFilePath(filepath: string): string {
  const segments = filepath.split("/");

  if (segments.some((segment) => segment === "." || segment === "..")) {
    throw new Error(`Refusing a path with a dot segment: ${filepath}`);
  }

  const encoded = segments.map(encodeURIComponent).join("/");

  return encoded.startsWith("/") ? `%2F${encoded.slice(1)}` : encoded;
}
