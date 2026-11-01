/** HTTP client for hivegent's chat endpoints (Vercel AI SDK request contract). */


import type { UIMessage } from "ai";

import type { BridgeConfig } from "../config.js";

/** Raised when a conversation id belongs to another identity (HTTP 404). */
export class ConversationNotFoundError extends Error {}

interface ChatRequestOptions {
  reasoningEffort?: string | undefined;
  disabledTools: string[];
}

interface ChatRequestBody {
  trigger: "submit-message";
  id: string;
  messages: UIMessage[];
  mode: "read";
  reasoning_effort?: string | undefined;
  tools: { disabled_tools: string[] };
}

/**
 * Build the minimal schema-valid request body for one new user turn. History is
 * server-authoritative, so only the newest message is sent, under a fresh UUID
 * that hivegent stores it as. The top-level `id` is required by the schema but
 * ignored by hivegent, which uses the URL path id.
 * The bot only answers questions, so it always runs in read mode, which keeps
 * every workspace out of reach of the write tools whatever its grants allow.
 */
export function buildRequestBody(text: string, opts: ChatRequestOptions): ChatRequestBody {
  return {
    trigger: "submit-message",
    id: crypto.randomUUID(),
    messages: [{ id: crypto.randomUUID(), role: "user", parts: [{ type: "text", text }] }],
    mode: "read",
    reasoning_effort: opts.reasoningEffort,
    tools: { disabled_tools: opts.disabledTools },
  };
}

function authHeaders(token: string | undefined): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  return headers;
}

// Bounds the wait for the response headers only, so a hung backend fails the
// turn instead of stalling the thread's queue, while the answer may stream longer.
const HEADERS_TIMEOUT_MS = 60_000;

async function postChat(
  path: string,
  text: string,
  token: string | undefined,
  cfg: BridgeConfig,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort(new Error("hivegent did not respond in time"));
  }, HEADERS_TIMEOUT_MS);

  try {
    return await fetch(`${cfg.hivegentUrl}${path}`, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(
        buildRequestBody(text, {
          reasoningEffort: cfg.reasoningEffort,
          disabledTools: cfg.disabledTools,
        }),
      ),
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

    throw new Error(`hivegent chat failed (${response.status}): ${detail}`);
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

  return response;
}
