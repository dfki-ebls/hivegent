import { mutationOptions, type QueryClient, queryOptions } from "@tanstack/react-query";
import { validateUIMessages } from "ai";
import { z } from "zod";

import { jsonRequest, requestBlob, requestJson, requestVoid } from "@/api/client";
import { apiUrl } from "@/lib/health";
import { type ChatMessage, messageSchemas } from "@/lib/chat/message";
import type { RagoldRequest } from "@/lib/ragold";
import { onConversationsStale } from "@/stores/jobs-store";

/** Summary information for listing conversations. */
export const ConversationSummarySchema = z.object({
  id: z.string(),
  title: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type ConversationSummary = z.infer<typeof ConversationSummarySchema>;

const ConversationListResponseSchema = z.object({
  conversations: z.array(ConversationSummarySchema),
});

const GenerateTitleResponseSchema = z.object({ title: z.string() });
type GenerateTitleResponse = z.infer<typeof GenerateTitleResponseSchema>;

/*
 * Conversation export and import interchange, mirroring `hivegent.types`.
 * The archive is passed through to a download, so its messages stay unchecked.
 */

/** A conversation's active path as the database holds it, with its prompts. */
const ServerConversationSchema = z.object({
  id: z.string().nullable(),
  title: z.string().nullable(),
  messages: z.array(z.custom<ChatMessage>()),
  /** Each composed system prompt and the messages sent under it. */
  instructions: z.array(z.object({ message_ids: z.array(z.string()), text: z.string() })),
  /** Compact Ed25519-signed JWT marking the conversation as AI-generated. */
  provenance: z.string().nullable(),
});
type ServerConversation = z.infer<typeof ServerConversationSchema>;

/** A conversation exactly as the browser tab held it, errors included. */
interface ClientConversation {
  id: string;
  title: string | null;
  exported_at: string;
  error: string | null;
  messages: ChatMessage[];
}

/** Both halves of an exported conversation, either may be absent. */
export interface ConversationArchive {
  backend: ServerConversation | null;
  frontend: ClientConversation | null;
}

/** Every agent mode, in display order. Labels live in the `options.mode` catalog section. */
export const AGENT_MODES = ["interactive", "read", "write"] as const;

/**
 * Agent mode controlling which tools are offered and how writes are gated.
 *
 * `interactive` (the default) offers the write tools but asks for confirmation
 * before every call, `read` withholds them entirely, and `write` runs them
 * unattended.
 */
export type AgentMode = (typeof AGENT_MODES)[number];

/** Selectable reasoning efforts, in display order. Labels live in `options.reasoningEffort`. */
export const REASONING_EFFORTS = ["low", "medium", "high"] as const;

export type ReasoningEffort = (typeof REASONING_EFFORTS)[number];

/** Mirrors the server default of `Preferences.reasoning_effort`. */
export const DEFAULT_REASONING_EFFORT: ReasoningEffort = "medium";

/** The level of *levels* nearest *effort*, the lower on a tie, as the server clamps it. */
export function clampReasoningEffort(
  effort: ReasoningEffort,
  levels: readonly ReasoningEffort[],
): ReasoningEffort | undefined {
  const rank = (level: ReasoningEffort) => REASONING_EFFORTS.indexOf(level);
  const distance = (level: ReasoningEffort) => Math.abs(rank(level) - rank(effort));

  return [...levels].sort((a, b) => distance(a) - distance(b) || rank(a) - rank(b))[0];
}

/**
 * What a chat request may add to the AI SDK's own body, `id`, `messages`,
 * `trigger`, and `messageId`, mirroring the backend's `ChatRequest`, which
 * refuses any other field: a user message names its documents in its metadata,
 * and everything else a run applies is the user's preferences the server keeps.
 */
export interface ChatRequestBody {
  /** Summarize the conversation instead of answering, sent only by the compact action. */
  compact?: boolean;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The API URL of *conversationId*'s route *rest*, refusing an id that is no UUID and so names none. */
function conversationUrl(conversationId: string, rest = ""): string {
  if (!UUID.test(conversationId)) {
    throw new Error(`Not a conversation id: ${conversationId}`);
  }

  return apiUrl(`/conversations/${conversationId}${rest}`);
}

/** The route every turn of a conversation is sent to, its first one creates the conversation. */
export function chatUrl(conversationId: string): string {
  return conversationUrl(conversationId, "/chat");
}

/** The running turn of a conversation, which a reload follows again and a stop ends. */
export function turnUrl(conversationId: string): string {
  return conversationUrl(conversationId, "/stream");
}

/** Stop the running turn on the server, where it runs on after the client stopped reading. */
export function stopTurn(conversationId: string): Promise<void> {
  return requestVoid(turnUrl(conversationId), "stopTurn", { method: "DELETE" });
}

function listConversations(): Promise<ConversationSummary[]> {
  return requestJson(
    apiUrl("/conversations"),
    "listConversations",
    ConversationListResponseSchema,
  ).then(({ conversations }) => conversations);
}

/** Stored *messages*, validated like the ones the chat streams. */
export function parseMessages(messages: unknown[]): Promise<ChatMessage[]> {
  // The validation rejects an empty list.
  return messages.length === 0
    ? Promise.resolve([])
    : validateUIMessages<ChatMessage>({ messages, ...messageSchemas });
}

/**
 * The stored messages of a conversation.
 * None for an id that names no conversation yet, which is how a new chat starts.
 */
export async function getConversationMessages(conversationId: string): Promise<ChatMessage[]> {
  return parseMessages(
    await requestJson(
      conversationUrl(conversationId, "/messages"),
      "fetchMessages",
      z.array(z.unknown()),
    ),
  );
}

function updateConversationTitle(
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  return requestJson(
    conversationUrl(conversationId),
    "update",
    ConversationSummarySchema,
    jsonRequest("PATCH", { title }),
  );
}

function generateConversationTitle(conversationId: string): Promise<GenerateTitleResponse> {
  return requestJson(
    conversationUrl(conversationId, "/title/generation"),
    "generateTitle",
    GenerateTitleResponseSchema,
    { method: "POST" },
  );
}

/**
 * Restore a previously exported conversation as a new chat owned by the user,
 * at an id minted here, so a retried request lands the same conversation.
 */
async function importConversation(file: File): Promise<ConversationSummary> {
  return requestJson(
    apiUrl(`/conversations/${crypto.randomUUID()}/archive`),
    "importConversation",
    ConversationSummarySchema,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: await file.text(),
    },
  );
}

function deleteConversation(conversationId: string): Promise<void> {
  return requestVoid(conversationUrl(conversationId), "delete", { method: "DELETE" });
}

/** Delete all conversations for the authenticated user. */
export function deleteAllConversations(): Promise<void> {
  return requestVoid(apiUrl("/conversations"), "deleteConversations", { method: "DELETE" });
}

/**
 * The persisted half of a conversation, for the export archive.
 *
 * Carries the system prompts each turn ran under, which exist only server-side.
 * Null when the conversation was never stored or the fetch fails, so exporting
 * still yields the client half rather than nothing.
 */
export async function getServerConversation(
  conversationId: string,
): Promise<ServerConversation | null> {
  try {
    const { backend } = await requestJson(
      conversationUrl(conversationId, "/archive"),
      "fetchMessages",
      z.object({ backend: ServerConversationSchema.nullable() }),
    );

    return backend;
  } catch {
    return null;
  }
}

/** Build the RAGold archive of one turn: its gold entry and the original documents. */
export function exportRagold(conversationId: string, request: RagoldRequest): Promise<Blob> {
  return requestBlob(
    conversationUrl(conversationId, "/ragold"),
    "exportRagold",
    jsonRequest("POST", request),
  );
}

const all = ["conversations"] as const;

export const conversationQueries = {
  /** The user's conversations, newest first. */
  list: () => queryOptions({ queryKey: [...all, "list"], queryFn: listConversations }),
  /**
   * The stored messages of a conversation, read afresh on every visit and
   * dropped right after, since the chat owns them from then on.
   */
  messages: (id: string) =>
    queryOptions({
      queryKey: [...all, "messages", id],
      queryFn: () => getConversationMessages(id),
      gcTime: 0,
    }),
};

// Coalesces a burst of feed events, such as a turn's title and its end, into one read.
const FEED_REFETCH_DELAY_MS = 250;

/**
 * Refetch the conversation list of *client* whenever the feed says it may have
 * changed, which every mutation, run, and import does, from any tab or device,
 * once for a burst of changes. Returns the unsubscribe.
 */
export function syncConversationQueries(client: QueryClient): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const unsubscribe = onConversationsStale(() => {
    clearTimeout(timer);
    timer = setTimeout(
      () => void client.invalidateQueries({ queryKey: conversationQueries.list().queryKey }),
      FEED_REFETCH_DELAY_MS,
    );
  });

  return () => {
    clearTimeout(timer);
    unsubscribe();
  };
}

// A mutation refetches the list itself, since the feed may be reconnecting.
const changesList = { invalidates: [conversationQueries.list().queryKey] };

export const conversationMutations = {
  delete: () =>
    mutationOptions({
      mutationKey: [...all, "delete"],
      mutationFn: deleteConversation,
      meta: changesList,
    }),
  /** Rename a conversation, its new summary shown in the list at once. */
  rename: () =>
    mutationOptions({
      mutationKey: [...all, "rename"],
      mutationFn: async ({ id, title }: { id: string; title: string }, { client }) => {
        const renamed = await updateConversationTitle(id, title);
        client.setQueryData(conversationQueries.list().queryKey, (list) =>
          list?.map((summary) => (summary.id === id ? renamed : summary)),
        );

        return renamed;
      },
      meta: changesList,
    }),
  generateTitle: () =>
    mutationOptions({
      mutationKey: [...all, "generate-title"],
      mutationFn: generateConversationTitle,
      meta: changesList,
    }),
  import: () =>
    mutationOptions({
      mutationKey: [...all, "import"],
      mutationFn: importConversation,
      meta: changesList,
    }),
};
