import { z } from "zod";

import { i18n, LANGUAGE } from "@/i18n";
import type { app } from "@/i18n/locales/en/app";
import { waitForBackendReady } from "@/lib/health";
import { getImpersonation, IMPERSONATE_HEADER } from "@/lib/impersonation";
import { getOidc } from "@/oidc";

const CLIENT_ID_HEADER = "X-Client-Id";

/**
 * Identifies this tab for the lifetime of the page. Sent on every request, so
 * the server can skip echoing a change back over the job feed to the very tab
 * whose request caused it, since that one re-reads the result itself while the
 * user's other tabs need telling.
 */
const CLIENT_ID = crypto.randomUUID();

/** The auth headers of every request. */
async function getAuthHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {
    [CLIENT_ID_HEADER]: CLIENT_ID,
    // The backend answers in this language, prompts and tool schemas included.
    "Accept-Language": LANGUAGE,
  };
  const oidc = await getOidc();

  if (oidc.isUserLoggedIn) {
    headers.Authorization = `Bearer ${await oidc.getAccessToken()}`;
  }

  const impersonation = getImpersonation();

  if (impersonation) {
    headers[IMPERSONATE_HEADER] = impersonation;
  }

  return headers;
}

/** An authenticated fetch, which waits for the backend to be ready first. */
export async function authFetch(
  url: RequestInfo | URL,
  options: RequestInit = {},
): Promise<Response> {
  // Startup fetches never race a backend that is still booting. The probe runs
  // once per app lifetime, so this is a no-op once ready.
  await waitForBackendReady();

  const headers = new Headers(options.headers);

  for (const [key, value] of Object.entries(await getAuthHeaders())) {
    headers.set(key, value);
  }

  return fetch(url, { ...options, headers });
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

/** A request the backend answered with an error status. */
export class HttpError extends Error {
  constructor(
    message: string | undefined,
    readonly status: number,
  ) {
    super(message);
  }
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

  if (problem?.type === MAINTENANCE_PROBLEM) {
    return new MaintenanceError(problem.detail, res.status);
  }

  return new HttpError(problem?.detail || fallbackMessage(error, res.status), res.status);
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

/** The fetch of a chat transport, which maps errors like every other request. */
export const chatFetch: typeof fetch = (input, init) => checkedResponse(input, "chat", init);

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
 */
export function encodeFilePath(filepath: string): string {
  const encoded = filepath.split("/").map(encodeURIComponent).join("/");

  return encoded.startsWith("/") ? `%2F${encoded.slice(1)}` : encoded;
}
