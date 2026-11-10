import { mutationOptions, queryOptions } from "@tanstack/react-query";
import { validateUIMessages } from "ai";
import { z } from "zod";

import { jsonRequest, requestBlob, requestJson, requestVoid } from "@/api/client";
import { apiUrl } from "@/lib/health";
import type { LlmConfig } from "@/api/settings";
import type { ToolsPayload } from "@/api/tools";
import { type ChatMessage, messageSchemas } from "@/lib/chat/message";
import type { RagoldRequest } from "@/lib/ragold";

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

/** Available assistant personalities. */
export const PersonalitySchema = z.enum(["default", "concise", "detailed", "structured", "custom"]);
export type Personality = z.infer<typeof PersonalitySchema>;

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

/** Mirrors the server default of `ChatRequestConfig.reasoning_effort`. */
export const DEFAULT_REASONING_EFFORT: ReasoningEffort = "medium";

/**
 * The settings of a chat request, mirroring `ChatRequestConfig` on the backend.
 *
 * The backend's `ChatRequest` adds the AI SDK's own fields, `id`, `messages`,
 * `trigger`, and `messageId`, which address a turn rather than configure it, and
 * the transport splices them into the body itself. It refuses any other field.
 */
export interface ChatRequestConfig {
  personality: Personality;
  system_message?: string;
  mode: AgentMode;
  llm: LlmConfig;
  included_documents: string[];
  excluded_documents: string[];
  tools: ToolsPayload;
  reasoning_effort: ReasoningEffort;
  /** Summarize the conversation instead of answering, sent only by the compact action. */
  compact?: boolean;
}

/** The route every turn of a conversation is sent to, its first one creates the conversation. */
export function chatUrl(conversationId: string): string {
  return apiUrl(`/conversations/${conversationId}/chat`);
}

/** The running turn of a conversation, which a reload follows again and a stop ends. */
export function turnUrl(conversationId: string): string {
  return apiUrl(`/conversations/${conversationId}/stream`);
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

/**
 * The stored messages of a conversation, validated like the ones the chat streams.
 * None for an id that names no conversation yet, which is how a new chat starts.
 */
export async function getConversationMessages(conversationId: string): Promise<ChatMessage[]> {
  const stored = await requestJson(
    apiUrl(`/conversations/${conversationId}/messages`),
    "fetchMessages",
    z.array(z.unknown()),
  );

  // The validation rejects an empty list.
  return stored.length === 0
    ? []
    : validateUIMessages<ChatMessage>({ messages: stored, ...messageSchemas });
}

function updateConversationTitle(
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  return requestJson(
    apiUrl(`/conversations/${conversationId}`),
    "update",
    ConversationSummarySchema,
    jsonRequest("PATCH", { title }),
  );
}

function generateConversationTitle(
  conversationId: string,
  llm: LlmConfig,
): Promise<GenerateTitleResponse> {
  return requestJson(
    apiUrl(`/conversations/${conversationId}/title/generation`),
    "generateTitle",
    GenerateTitleResponseSchema,
    jsonRequest("POST", { llm }),
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
  return requestVoid(apiUrl(`/conversations/${conversationId}`), "delete", { method: "DELETE" });
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
      apiUrl(`/conversations/${conversationId}/archive`),
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
    apiUrl(`/conversations/${conversationId}/ragold`),
    "exportRagold",
    jsonRequest("POST", request),
  );
}

const all = ["conversations"] as const;

export const conversationQueries = {
  /** The user's conversations, newest first. */
  list: () => queryOptions({ queryKey: [...all, "list"], queryFn: listConversations }),
};

// Every conversation mutation can change the list, so each refetches it.
const meta = { invalidates: [conversationQueries.list().queryKey] };

export const conversationMutations = {
  delete: () =>
    mutationOptions({ mutationKey: [...all, "delete"], mutationFn: deleteConversation, meta }),
  rename: () =>
    mutationOptions({
      mutationKey: [...all, "rename"],
      mutationFn: ({ id, title }: { id: string; title: string }) =>
        updateConversationTitle(id, title),
      meta,
    }),
  generateTitle: () =>
    mutationOptions({
      mutationKey: [...all, "generate-title"],
      mutationFn: ({ id, llm }: { id: string; llm: LlmConfig }) =>
        generateConversationTitle(id, llm),
      meta,
    }),
  import: () =>
    mutationOptions({ mutationKey: [...all, "import"], mutationFn: importConversation, meta }),
};
