/** HTTP client for hivegent's chat endpoints (Vercel AI SDK request contract). */

import type { UIMessage } from "ai";
import { z } from "zod";

import type { BridgeConfig } from "../config.js";
import { fetchRetryable, HttpError } from "./retry.js";

/** Raised when a conversation id belongs to another identity (HTTP 404). */
export class ConversationNotFoundError extends Error {}

interface ChatRequestBody {
  trigger: "submit-message";
  id: string;
  messages: UIMessage[];
}

/**
 * Build the minimal schema-valid request body for one new user turn. History is
 * server-authoritative, so only the newest message is sent, under a fresh UUID
 * that hivegent stores it as. The top-level `id` is required by the schema but
 * ignored by hivegent, which uses the URL path id. hivegent runs the bridge's
 * service principal in read mode over the global workspace alone, with the
 * preferences it keeps for that principal.
 */
export function buildRequestBody(text: string): ChatRequestBody {
  return {
    trigger: "submit-message",
    id: crypto.randomUUID(),
    messages: [{ id: crypto.randomUUID(), role: "user", parts: [{ type: "text", text }] }],
  };
}

function authHeaders(token: string | undefined): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };

  // Declared on every request, so hivegent refuses a token that stopped being the service principal's.
  if (token) {
    headers.Authorization = `Bearer ${token}`;
    headers["X-Hivegent-Principal"] = "service";
  }

  return headers;
}

// Bounds the wait for the response headers only, so a hung backend fails the
// turn instead of stalling the thread's queue, while the answer may stream longer.
const HEADERS_TIMEOUT_MS = 60_000;

/** POST *body* to *path*, or GET it without one. */
async function send(
  path: string,
  token: string | undefined,
  cfg: BridgeConfig,
  body?: ChatRequestBody,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort(new Error("hivegent did not respond in time"));
  }, HEADERS_TIMEOUT_MS);

  try {
    return await fetch(`${cfg.backendUrl}${path}`, {
      method: body ? "POST" : "GET",
      headers: authHeaders(token),
      body: body && JSON.stringify(body),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

async function ensureOk(response: Response): Promise<void> {
  if (!response.ok) {
    let detail = "<unreadable body>";

    try {
      detail = (await response.text()).slice(0, 500);
    } catch {
      // Keep the placeholder.
    }

    throw new HttpError(response.status, `hivegent request failed (${response.status}): ${detail}`);
  }
}

const Principal = z.object({ kind: z.enum(["person", "service"]) });

/**
 * Refuse to run unless the token authenticates as hivegent's service principal,
 * which hivegent grants only to `auth.service_client_id`.
 */
export async function requireServicePrincipal(cfg: BridgeConfig, token: string): Promise<void> {
  const response = await fetchRetryable(`${cfg.backendUrl}/api/v1/principal`, {
    headers: authHeaders(token),
    signal: AbortSignal.timeout(HEADERS_TIMEOUT_MS),
  });

  await ensureOk(response);

  const { kind } = Principal.parse(await response.json());

  if (kind !== "service") {
    throw new Error(
      `hivegent authenticates the bridge as a ${kind}, set auth.service_client_id to its OIDC client id`,
    );
  }
}

/**
 * Run a turn in a conversation, which hivegent starts when it holds no
 * conversation with that id yet, so a new one is a fresh `crypto.randomUUID()`.
 */
export async function chat(
  conversationId: string,
  text: string,
  token: string | undefined,
  cfg: BridgeConfig,
): Promise<Response> {
  const response = await send(
    conversationPath(conversationId, "chat"),
    token,
    cfg,
    buildRequestBody(text),
  );

  if (response.status === 404) {
    throw new ConversationNotFoundError(conversationId);
  }

  await ensureOk(response);

  return response;
}

/** Follow the running turn of a conversation again from its first chunk, `null` once it ended. */
export async function follow(
  conversationId: string,
  token: string | undefined,
  cfg: BridgeConfig,
): Promise<Response | null> {
  const response = await send(conversationPath(conversationId, "stream"), token, cfg);

  if (response.status === 204) {
    return null;
  }

  await ensureOk(response);

  return response;
}

function conversationPath(conversationId: string, action: "chat" | "stream"): string {
  return `/api/v1/conversations/${encodeURIComponent(conversationId)}/${action}`;
}
