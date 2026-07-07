/** HTTP client for hivegent's chat endpoints (Vercel AI SDK request contract). */

import { randomUUID } from "node:crypto";

import type { UIMessage } from "ai";

import type { BridgeConfig } from "../config.js";

/** Raised when a mapped conversation id no longer exists server-side (HTTP 404). */
export class ConversationNotFoundError extends Error {}

interface ChatRequestOptions {
  reasoningEffort: string;
  disabledTools: string[];
}

interface ChatRequestBody {
  trigger: "submit-message";
  id: string;
  messages: UIMessage[];
  reasoning_effort: string;
  tools: { disabled_tools: string[] };
}

/**
 * Build the minimal schema-valid request body for one new user turn. History is
 * server-authoritative, so only the newest message is sent; the top-level `id` is
 * required by the schema but ignored by hivegent (it uses the URL path id).
 */
export function buildRequestBody(text: string, opts: ChatRequestOptions): ChatRequestBody {
  return {
    trigger: "submit-message",
    id: randomUUID(),
    messages: [{ id: randomUUID(), role: "user", parts: [{ type: "text", text }] }],
    reasoning_effort: opts.reasoningEffort,
    tools: { disabled_tools: opts.disabledTools },
  };
}

export interface ChatResult {
  response: Response;
  conversationId: string;
}

function authHeaders(token: string | undefined): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  return headers;
}

function postChat(
  path: string,
  text: string,
  token: string | undefined,
  cfg: BridgeConfig,
): Promise<Response> {
  return fetch(`${cfg.hivegentUrl}${path}`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(
      buildRequestBody(text, {
        reasoningEffort: cfg.reasoningEffort,
        disabledTools: cfg.disabledTools,
      }),
    ),
  });
}

async function ensureOk(response: Response): Promise<void> {
  if (!response.ok) {
    let detail = "<unreadable body>";

    try {
      detail = (await response.text()).slice(0, 500);
    } catch {
      // Keep the placeholder.
    }

    throw new Error(`hivegent chat failed (${response.status}): ${detail}`);
  }
}

/** Start a new conversation; the server mints the id and returns it in a header. */
export async function startConversation(
  text: string,
  token: string | undefined,
  cfg: BridgeConfig,
): Promise<ChatResult> {
  const response = await postChat("/api/conversations/chat", text, token, cfg);
  await ensureOk(response);

  const conversationId = response.headers.get("X-Conversation-Id");

  if (!conversationId) {
    throw new Error("hivegent did not return an X-Conversation-Id header");
  }

  return { response, conversationId };
}

/** Continue an existing conversation; a 404 signals a stale mapping. */
export async function continueConversation(
  conversationId: string,
  text: string,
  token: string | undefined,
  cfg: BridgeConfig,
): Promise<ChatResult> {
  const response = await postChat(
    `/api/conversations/${encodeURIComponent(conversationId)}/chat`,
    text,
    token,
    cfg,
  );

  if (response.status === 404) {
    throw new ConversationNotFoundError(conversationId);
  }

  await ensureOk(response);

  return { response, conversationId };
}
