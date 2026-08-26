import type { ChatMessage } from "@/lib/chat/chat-utils";
import { z } from "zod";

import type { AgentMode, McpServerEntry, ToolsSpec } from "@/lib/types";
import {
  type AdminFactoryResetResponse,
  AdminFactoryResetResponseSchema,
  type AdminGroupInfo,
  AdminListGroupsResponseSchema,
  AdminListUsersResponseSchema,
  AdminMaintenanceStateSchema,
  type AdminReindexResponse,
  AdminReindexResponseSchema,
  type AdminResetResponse,
  AdminResetResponseSchema,
  type AdminUserInfo,
  type AssetEntry,
  AssetEntrySchema,
  type AssetListResponse,
  AssetListResponseSchema,
  type BackendSettings,
  BackendSettingsSchema,
  type ChunkedDocumentResponse,
  ChunkedDocumentResponseSchema,
  type ChunkingPipeline,
  type ChunkingPipelineInfo,
  ChunkingPipelineInfoSchema,
  type CompactConversationResponse,
  CompactConversationResponseSchema,
  ConversationListResponseSchema,
  DocumentLineCountsResponseSchema,
  type ConversationSummary,
  type ConversationArchive,
  ConversationSummarySchema,
  type ServerConversation,
  type ConversionPipeline,
  type ConversionPipelineInfo,
  ConversionPipelineInfoSchema,
  type DirectoryTreeResponse,
  DirectoryTreeResponseSchema,
  type GenerateTitleResponse,
  GenerateTitleResponseSchema,
  FeedEventSchema,
  type JobView,
  JobViewSchema,
  type LlmConfig,
  type PipelineConfigInfo,
  PipelineConfigInfoSchema,
  type ScratchClearedResponse,
  ScratchClearedResponseSchema,
  type ToolInfo,
  ToolInfoSchema,
  type ToolRunResult,
  ToolRunResultSchema,
  type ToolSchema,
  ToolSchemaSchema,
  TranscriptionResponseSchema,
  type PipelineSpec,
} from "@/lib/types";

import { featureFlags } from "@/lib/feature-flags";

import { API_BASE_URL, waitForBackendReady } from "@/lib/health";
import { getImpersonation, IMPERSONATE_HEADER } from "@/lib/impersonation";
import { nanoid } from "nanoid";
import { getOidc } from "@/oidc";

/**
 * Make an authenticated fetch request.
 */
async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  // Gate every authenticated request on backend readiness so startup fetches
  // (settings, the job feed, ...) never race a backend that is still booting
  // and spam the dev proxy with connection-refused errors. The probe runs once
  // per app lifetime (see waitForBackendReady), so this is a no-op once ready.
  await waitForBackendReady();

  const headers = new Headers(options.headers);

  for (const [key, value] of Object.entries(await getAuthHeaders())) {
    headers.set(key, value);
  }

  return fetch(url, { ...options, headers });
}

/**
 * Human explanation for the edge statuses that reach the client without a JSON
 * `detail` body: the reverse proxy (Caddy), not the API, produced them — so the
 * bare `(HTTP nnn)` fallback leaves the user guessing. Returns null when a status
 * carries no more meaning than itself, so the caller keeps its own fallback.
 */
function statusExplanation(status: number): string | null {
  switch (status) {
    case 413:
      return "This upload is too large. Please choose a smaller file, or split it into several parts.";
    case 429:
      return "Too many requests in a short time. Please wait a moment and try again.";
    case 502:
    case 503:
    case 504:
      return "The server is temporarily unavailable, likely restarting. Please try again in a moment.";
    default:
      return null;
  }
}

/**
 * Message for a failure with no usable `detail` body: a friendly explanation for
 * a known edge status, otherwise `fallback`, always suffixed with `(HTTP nnn)` so
 * the status stays visible for diagnostics.
 */
function fallbackMessage(fallback: string, status: number): string {
  return `${statusExplanation(status) ?? fallback} (HTTP ${status})`;
}

/** Thrown when the backend rejects a request through the maintenance gate. */
export class MaintenanceError extends Error {}

async function responseError(res: Response, fallback: string): Promise<Error> {
  const body = (await res.json().catch(() => null)) as {
    detail?: string | { code?: string; message?: string };
  } | null;
  if (
    res.status === 503 &&
    typeof body?.detail === "object" &&
    body.detail.code === "maintenance"
  ) {
    return new MaintenanceError(body.detail.message);
  }

  return new Error(
    typeof body?.detail === "string" && body.detail
      ? body.detail
      : fallbackMessage(fallback, res.status),
  );
}

async function checkedResponse(
  url: string,
  errorMessage: string,
  options?: RequestInit,
): Promise<Response> {
  const response = await authFetch(url, options);
  if (!response.ok) throw await responseError(response, errorMessage);
  return response;
}

async function requestJson<T>(
  url: string,
  errorMessage: string,
  schema: z.ZodType<T>,
  options?: RequestInit,
): Promise<T> {
  const response = await checkedResponse(url, errorMessage, options);
  return schema.parse(await response.json());
}

async function requestVoid(
  url: string,
  errorMessage: string,
  options?: RequestInit,
): Promise<void> {
  await checkedResponse(url, errorMessage, options);
}

async function requestText(url: string, errorMessage: string): Promise<string> {
  return (await checkedResponse(url, errorMessage)).text();
}

async function requestBlob(url: string, errorMessage: string): Promise<Blob> {
  return (await checkedResponse(url, errorMessage)).blob();
}

function jsonRequest(method: "POST" | "PUT" | "PATCH" | "DELETE", body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** POST a JSON body to a job endpoint and return the started job's snapshot. */
async function postJob(url: string, body: unknown, errorMsg: string): Promise<JobView> {
  return requestJson(url, errorMsg, JobViewSchema, jsonRequest("POST", body));
}

const CLIENT_ID_HEADER = "X-Client-Id";

/**
 * Identifies this tab for the lifetime of the page. Sent on every request, so
 * the server can skip echoing a change back over the job feed to the very tab
 * whose request caused it — that one re-reads the result itself, while the
 * user's other tabs need telling.
 */
const CLIENT_ID = nanoid();

/**
 * Get the current auth headers for use with external transports.
 */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = { [CLIENT_ID_HEADER]: CLIENT_ID };
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

/**
 * Encode a file path for use in URLs.
 * Encodes each segment individually so that `/` separators are preserved.
 */
function encodeFilePath(filepath: string): string {
  return filepath.split("/").map(encodeURIComponent).join("/");
}

/**
 * Canonical scope of the caller's personal workspace.
 *
 * Scopes are the single source of truth for addressing a workspace: `~` for
 * the personal one, `@<group>` for a group (see {@link groupScope}). The same
 * string keys the document store, flows through the API, and is what the
 * backend resolves — there is no separate "personal vs group" representation.
 */
export const PERSONAL_SCOPE = "~";

/**
 * Canonical scope of a group's workspace.
 *
 * Always built from `GroupInfo.id`, never the display name: the id is the
 * group's only identity, so a path keeps addressing the same workspace after
 * a rename. `GroupInfo.name` is for labels.
 */
export function groupScope(groupId: string): string {
  return `@${groupId}`;
}

/**
 * Compose the canonical path of a document from its scope and local path
 * (e.g. `~/notes.md`, `@research/notes.md`). An empty local yields the bare
 * scope root, so this is the exact inverse of {@link splitScopePath}.
 */
export function canonicalPath(scope: string, local: string): string {
  return local ? `${scope}/${local}` : scope;
}

/**
 * Split a canonical directory into its scope and workspace-relative subpath —
 * the inverse of {@link canonicalPath}. The scope root (`~`, `@group`) yields an
 * empty local; `~/a/b` yields `{ scope: "~", local: "a/b" }`.
 */
export function splitScopePath(canonical: string): { scope: string; local: string } {
  const slash = canonical.indexOf("/");
  return slash === -1
    ? { scope: canonical, local: "" }
    : { scope: canonical.slice(0, slash), local: canonical.slice(slash + 1) };
}

/**
 * Human-readable breadcrumb for a canonical directory, e.g. `~/a/b` becomes
 * "~ / a / b" and `@research` becomes "research". Shared by every surface
 * that names the active target (upload area, create dialogs).
 */
export function formatTarget(target: string): string {
  const { scope, local } = splitScopePath(target);
  const scopeLabel = scope.startsWith("@") ? scope.slice(1) : scope;
  return local ? `${scopeLabel} / ${local.replaceAll("/", " / ")}` : scopeLabel;
}

/** Check if a file requires conversion (anything that is not already markdown). */
export function requiresConversion(filename: string): boolean {
  const ext = `.${filename.split(".").pop()?.toLowerCase() ?? ""}`;
  return ext !== ".md";
}

/** Fetch server-side LLM settings. */
export async function getSettings(): Promise<BackendSettings> {
  return requestJson(
    `${API_BASE_URL}/api/settings`,
    "Failed to fetch settings",
    BackendSettingsSchema,
  );
}

/** Transcribe recorded audio via the backend STT endpoint. */
export async function transcribeAudio(audio: Blob): Promise<string> {
  const form = new FormData();
  form.append("audio", audio, "recording.webm");
  return (
    await requestJson(
      `${API_BASE_URL}/api/transcription`,
      "Failed to transcribe audio",
      TranscriptionResponseSchema,
      { method: "POST", body: form },
    )
  ).text;
}

/** Fetch available agent tools from the backend. */
export async function listTools(): Promise<ToolInfo[]> {
  return requestJson(`${API_BASE_URL}/api/tools`, "Failed to fetch tools", z.array(ToolInfoSchema));
}

/** Fetch every agent tool with its parameter JSON Schema (admin only). */
export async function listToolSchemas(): Promise<ToolSchema[]> {
  return requestJson(
    `${API_BASE_URL}/api/debug/tools`,
    "Failed to fetch tool schemas",
    z.array(ToolSchemaSchema),
  );
}

/** Invoke an agent tool with arbitrary arguments (admin only). */
export async function runTool(name: string, args: Record<string, unknown>): Promise<ToolRunResult> {
  return requestJson(
    `${API_BASE_URL}/api/debug/tools/${encodeURIComponent(name)}`,
    "Failed to run tool",
    ToolRunResultSchema,
    jsonRequest("POST", args),
  );
}

/** Response from testing an MCP server connection. */
export interface McpTestResult {
  ok: boolean;
  tool_count: number | null;
  error: string | null;
}

/** Test connectivity to an MCP server. */
export async function testMcpServer(server: McpServerEntry): Promise<McpTestResult> {
  const res = await authFetch(`${API_BASE_URL}/api/mcp/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: server.url,
      headers: server.headers,
      tool_prefix: server.toolPrefix ?? null,
      oauth2: server.oauth2
        ? {
            client_id: server.oauth2.clientId,
            client_secret: server.oauth2.clientSecret,
            scopes: server.oauth2.scopes ?? null,
          }
        : null,
    }),
  });

  if (!res.ok) {
    return { ok: false, tool_count: null, error: "Request failed" };
  }
  return (await res.json()) as McpTestResult;
}

/**
 * Build a sparse LlmConfig from frontend settings.
 *
 * When the {@link featureFlags.llmSpec} flag is disabled, returns an empty
 * config regardless of the input — LLM provider customization is a
 * frontend-only feature, so disabling it implicitly forces every outgoing
 * request to use the backend's configured defaults.
 */
export function buildLlmConfig(s: {
  model?: string;
  apiKey?: string;
  baseUrl?: string;
}): LlmConfig {
  if (!featureFlags.llmSpec) return {};
  const config: LlmConfig = {};
  if (s.model) config.model = s.model;
  if (s.apiKey) config.api_key = s.apiKey;
  if (s.baseUrl) config.base_url = s.baseUrl;
  return config;
}

/** LLM config for auxiliary tasks (titles, captions): prefer aux model, fall back to primary. */
export function buildAuxLlmConfig(overrides: {
  model: string;
  auxModel: string;
  apiKey: string;
  baseUrl: string;
}): LlmConfig {
  return buildLlmConfig({
    model: overrides.auxModel || overrides.model,
    apiKey: overrides.apiKey,
    baseUrl: overrides.baseUrl,
  });
}

/**
 * Build the agent mode value sent to the backend.
 *
 * When the {@link featureFlags.agentModes} flag is disabled, always returns
 * `"interactive"` — mode selection is a frontend-only feature, so disabling
 * it implicitly pins the backend to the confirm-before-writing default
 * without a parallel flag system.
 */
export function buildModePayload(mode: AgentMode): AgentMode {
  if (!featureFlags.agentModes) return "interactive";
  return mode;
}

/**
 * Convert a frontend ToolsSpec to the snake_case backend payload.
 *
 * When the {@link featureFlags.toolsSpec} flag is disabled, returns an
 * empty payload regardless of the stored spec — tool/MCP customization is
 * a frontend-only feature, so disabling it implicitly removes the data
 * from every outgoing request without touching the backend.
 */
export function buildToolsPayload(spec: ToolsSpec): Record<string, unknown> {
  if (!featureFlags.toolsSpec) {
    return { disabled_tools: [], mcp_servers: [] };
  }
  return {
    disabled_tools: spec.disabledTools,
    mcp_servers: spec.mcpServers.map((s) => ({
      url: s.url,
      headers: s.headers,
      tool_prefix: s.toolPrefix ?? null,
      oauth2: s.oauth2
        ? {
            client_id: s.oauth2.clientId,
            client_secret: s.oauth2.clientSecret,
            scopes: s.oauth2.scopes ?? null,
          }
        : null,
    })),
  };
}

/**
 * The persisted half of a conversation, for the export archive.
 *
 * Carries the system prompts each turn ran under, which exist only server-side.
 * Returns `null` when the conversation has never been persisted (a draft) or the
 * fetch fails, so exporting still yields the client half rather than nothing.
 */
export async function getServerConversation(
  conversationId: string,
): Promise<ServerConversation | null> {
  try {
    const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/export`);
    if (!res.ok) return null;
    const data = (await res.json()) as ConversationArchive;
    return data.backend ?? null;
  } catch {
    return null;
  }
}

export async function getConversationMessages(conversationId: string): Promise<ChatMessage[]> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`);
  if (!res.ok) {
    return [];
  }
  const data: unknown = await res.json();
  if (!Array.isArray(data)) return [];
  return data as ChatMessage[];
}

// ============================================================
// User document API functions (authenticated user's personal documents)
// ============================================================

/** Options for document upload. */
export interface UploadDocumentOptions {
  spec?: PipelineSpec;
  llm?: LlmConfig;
  overwrite?: boolean;
}

/** Build the multipart body for a document upload PUT. */
function documentUploadFormData(
  filename: string,
  file: File,
  options?: UploadDocumentOptions,
): FormData {
  const formData = new FormData();
  formData.append("file", file);
  if (options?.overwrite) {
    formData.append("overwrite", "true");
  }
  if (options?.spec) {
    formData.append("pipeline_spec", JSON.stringify(options.spec));
  }
  if (requiresConversion(filename) && options?.llm) {
    formData.append("llm_config", JSON.stringify(options.llm));
  }
  return formData;
}

/**
 * Upload or replace a document and start its background job; `filename` is a
 * canonical path (`~/…` or `@<group>/…`). Resolves once the bytes are received
 * with the job's initial snapshot — conversion and indexing then run off the
 * request and are observable through the `/jobs` feed.
 */
export async function uploadDocument(
  filename: string,
  file: File,
  options?: UploadDocumentOptions & { signal?: AbortSignal },
): Promise<JobView> {
  return requestJson(
    `${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`,
    "Upload failed",
    JobViewSchema,
    {
      method: "PUT",
      body: documentUploadFormData(filename, file, options),
      signal: options?.signal,
    },
  );
}

/**
 * Write a markdown document's content; `filename` is a canonical path. Keeps the
 * document's original binary and assets and only rewrites the text and its
 * chunks. `mode` defaults to `"replace"` (overwrite or create); pass `"create"`
 * to reject an existing path with a 409 instead of overwriting it.
 */
export async function writeDocument(
  filename: string,
  content: string,
  chunking?: PipelineSpec["chunking"],
  mode: "replace" | "create" = "replace",
): Promise<void> {
  return requestVoid(
    `${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`,
    "Save failed",
    jsonRequest("PATCH", { content, mode, chunking: chunking ?? null }),
  );
}

/** Options for collection upload (ZIP or directory). */
export interface UploadCollectionOptions {
  spec?: PipelineSpec;
  llm?: LlmConfig;
}

/**
 * Upload a collection ZIP and start its background job; `target` is the
 * canonical directory the archive lands under — a scope root (`~`, `@<group>`)
 * or a subdir (`~/projects`). Resolves with the job's initial snapshot; per-file
 * progress then arrives through the `/jobs` feed.
 */
export async function uploadCollection(
  target: string,
  file: File,
  options?: UploadCollectionOptions & { signal?: AbortSignal },
): Promise<JobView> {
  const formData = new FormData();
  formData.append("file", file);
  if (options?.spec) {
    formData.append("pipeline_spec", JSON.stringify(options.spec));
  }
  if (options?.llm) {
    formData.append("llm_config", JSON.stringify(options.llm));
  }

  return requestJson(
    `${API_BASE_URL}/api/documents/collections/${encodeFilePath(target)}`,
    "Collection upload failed",
    JobViewSchema,
    { method: "POST", body: formData, signal: options?.signal },
  );
}

/**
 * Shared SSE stream reader.  Reads `data:` lines from a streaming response,
 * validates each against `schema`, and calls `onEvent` for every parsed event.
 * Returns the value stored by `onEvent` via its return value (non-undefined
 * means "this is the final result").
 */
async function readSseEvents<TEvent, TResult>(
  res: Response,
  schema: z.ZodType<TEvent>,
  onEvent: (event: TEvent) => TResult | undefined,
): Promise<TResult> {
  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  let result: TResult | undefined;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data: ")) continue;

      let event: TEvent;
      try {
        const raw: unknown = JSON.parse(trimmed.slice(6));
        event = schema.parse(raw);
      } catch {
        continue;
      }

      const r = onEvent(event);
      if (r !== undefined) result = r;
    }
  }

  if (result === undefined) throw new Error("Stream ended without completion event");
  return result;
}

export async function deleteDocument(filename: string): Promise<void> {
  return requestVoid(`${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`, "Delete failed", {
    method: "DELETE",
  });
}

export async function getDocumentContent(filename: string): Promise<string> {
  return requestText(
    `${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`,
    "Failed to fetch document content",
  );
}

/** Fetch a workspace asset (e.g. image) as a blob URL for display. */
export async function fetchDocumentAsset(filepath: string, signal?: AbortSignal): Promise<string> {
  const blob = await (
    await checkedResponse(
      `${API_BASE_URL}/api/documents/${encodeFilePath(filepath)}`,
      "Failed to fetch document asset",
      { signal },
    )
  ).blob();
  return URL.createObjectURL(blob);
}

/** Download the original binary file for a document. */
export async function downloadOriginal(filepath: string): Promise<Blob> {
  return requestBlob(
    `${API_BASE_URL}/api/documents/original/${encodeFilePath(filepath)}`,
    "Download failed",
  );
}

/**
 * Replace the original binary file and reconvert the document as a background
 * job; resolves with the job's initial snapshot. Progress arrives via the feed.
 */
export async function replaceOriginal(
  filepath: string,
  file: File,
  spec?: PipelineSpec,
  llm?: LlmConfig,
): Promise<JobView> {
  const formData = new FormData();
  formData.append("file", file);
  if (spec) formData.append("pipeline_spec", JSON.stringify(spec));
  if (llm) formData.append("llm_config", JSON.stringify(llm));

  return requestJson(
    `${API_BASE_URL}/api/documents/original/${encodeFilePath(filepath)}`,
    "Replace failed",
    JobViewSchema,
    { method: "PUT", body: formData },
  );
}

export async function listConversations(): Promise<ConversationSummary[]> {
  return (
    await requestJson(
      `${API_BASE_URL}/api/conversations`,
      "Failed to list conversations",
      ConversationListResponseSchema,
    )
  ).conversations;
}

export async function updateConversationTitle(
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  return requestJson(
    `${API_BASE_URL}/api/conversations/${conversationId}`,
    "Update failed",
    ConversationSummarySchema,
    jsonRequest("PATCH", { title }),
  );
}

export async function generateConversationTitle(
  conversationId: string,
  llm: LlmConfig,
): Promise<GenerateTitleResponse> {
  return requestJson(
    `${API_BASE_URL}/api/conversations/${conversationId}/title/generation`,
    "Title generation failed",
    GenerateTitleResponseSchema,
    jsonRequest("POST", { llm }),
  );
}

export async function compactConversation(
  conversationId: string,
  llm: LlmConfig,
  messages: ChatMessage[],
): Promise<CompactConversationResponse> {
  return requestJson(
    `${API_BASE_URL}/api/conversations/${conversationId}/compaction`,
    "Compaction failed",
    CompactConversationResponseSchema,
    jsonRequest("POST", { llm, messages }),
  );
}

export async function getConversation(conversationId: string): Promise<ConversationSummary | null> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`);
  if (res.status === 404) return null;
  if (!res.ok) throw await responseError(res, "Failed to fetch conversation");
  const data: unknown = await res.json();
  return ConversationSummarySchema.parse(data);
}

/** Restore a previously exported conversation as a new chat owned by the user. */
export async function importConversation(file: File): Promise<ConversationSummary> {
  return requestJson(
    `${API_BASE_URL}/api/conversations/import`,
    "Import failed",
    ConversationSummarySchema,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: await file.text() },
  );
}

export async function deleteConversation(conversationId: string): Promise<void> {
  return requestVoid(`${API_BASE_URL}/api/conversations/${conversationId}`, "Delete failed", {
    method: "DELETE",
  });
}

// Conversion pipeline API functions

export async function listConversionPipelines(): Promise<ConversionPipelineInfo[]> {
  return requestJson(
    `${API_BASE_URL}/api/pipelines/conversion`,
    "Failed to get conversion pipelines",
    z.array(ConversionPipelineInfoSchema),
  );
}

export async function getConversionPipelineConfig(
  pipeline: ConversionPipeline,
): Promise<PipelineConfigInfo> {
  return requestJson(
    `${API_BASE_URL}/api/pipelines/conversion/${encodeURIComponent(pipeline)}/config`,
    "Failed to get conversion pipeline configuration",
    PipelineConfigInfoSchema,
  );
}

// Chunking pipeline API functions

export async function listChunkingPipelines(): Promise<ChunkingPipelineInfo[]> {
  return requestJson(
    `${API_BASE_URL}/api/pipelines/chunking`,
    "Failed to get chunking pipelines",
    z.array(ChunkingPipelineInfoSchema),
  );
}

export async function getChunkingPipelineConfig(
  pipeline: ChunkingPipeline,
): Promise<PipelineConfigInfo> {
  return requestJson(
    `${API_BASE_URL}/api/pipelines/chunking/${encodeURIComponent(pipeline)}/config`,
    "Failed to get chunking pipeline configuration",
    PipelineConfigInfoSchema,
  );
}

export async function getDocumentChunks(filename: string): Promise<ChunkedDocumentResponse> {
  return requestJson(
    `${API_BASE_URL}/api/documents/chunks/${encodeFilePath(filename)}`,
    "Failed to fetch chunks",
    ChunkedDocumentResponseSchema,
  );
}

/** Batch-resolve document line counts; unknown paths are omitted from the map. */
export async function getDocumentLineCounts(files: string[]): Promise<Record<string, number>> {
  if (files.length === 0) return {};

  return (
    await requestJson(
      `${API_BASE_URL}/api/documents/line-counts`,
      "Failed to fetch line counts",
      DocumentLineCountsResponseSchema,
      jsonRequest("POST", { files }),
    )
  ).line_counts;
}

// Asset API functions

/** List assets for a document. */
export async function listDocumentAssets(filename: string): Promise<AssetListResponse> {
  return requestJson(
    `${API_BASE_URL}/api/documents/assets/${encodeFilePath(filename)}`,
    "Failed to list assets",
    AssetListResponseSchema,
  );
}

/** Update an asset's companion .md description. */
export async function updateAssetDescription(
  filename: string,
  assetName: string,
  content: string,
): Promise<AssetEntry> {
  return requestJson(
    `${API_BASE_URL}/api/documents/assets/${encodeFilePath(filename)}`,
    "Failed to update description",
    AssetEntrySchema,
    jsonRequest("PATCH", { asset_name: assetName, content }),
  );
}

/** Generate an asset's companion .md description with the vision model. */
export async function generateAssetDescription(
  filename: string,
  assetName: string,
  llm?: LlmConfig,
): Promise<AssetEntry> {
  return requestJson(
    `${API_BASE_URL}/api/documents/assets/${encodeFilePath(filename)}`,
    "Failed to generate description",
    AssetEntrySchema,
    jsonRequest("POST", { asset_name: assetName, llm: llm ?? {} }),
  );
}

/** Delete an asset's companion .md description, keeping the asset itself. */
export async function deleteAssetDescription(
  filename: string,
  assetName: string,
): Promise<AssetEntry> {
  return requestJson(
    `${API_BASE_URL}/api/documents/assets/${encodeFilePath(filename)}?asset_name=${encodeURIComponent(assetName)}`,
    "Failed to delete description",
    AssetEntrySchema,
    { method: "DELETE" },
  );
}

/** Options for document reconversion. */
export interface ReconvertDocumentOptions {
  spec?: PipelineSpec;
  llm?: LlmConfig;
}

/**
 * Rechunk a document with new settings as a background job; resolves with the
 * job's initial snapshot. Progress arrives via the `/jobs` feed.
 */
export async function rechunkDocument(filename: string, spec?: PipelineSpec): Promise<JobView> {
  return postJob(
    `${API_BASE_URL}/api/documents/rechunk/${encodeFilePath(filename)}`,
    spec ?? {},
    "Rechunk failed",
  );
}

/**
 * Reconvert a document from its original and start its background job.
 * Resolves with the job's initial snapshot; progress arrives via the feed.
 */
export async function reconvertDocument(
  filename: string,
  options?: ReconvertDocumentOptions & { signal?: AbortSignal },
): Promise<JobView> {
  return requestJson(
    `${API_BASE_URL}/api/documents/reconvert/${encodeFilePath(filename)}`,
    "Reconvert failed",
    JobViewSchema,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pipeline: options?.spec ?? {}, llm: options?.llm ?? {} }),
      signal: options?.signal,
    },
  );
}

// --- Background jobs (generic) ---

/** List the caller's known background jobs. */
export async function listJobs(): Promise<JobView[]> {
  return requestJson(`${API_BASE_URL}/api/jobs`, "Failed to list jobs", z.array(JobViewSchema));
}

/** Request cancellation of a background job. Idempotent server-side. */
export async function cancelJob(id: string): Promise<void> {
  return requestVoid(`${API_BASE_URL}/api/jobs/${encodeURIComponent(id)}`, "Failed to cancel job", {
    method: "DELETE",
  });
}

/**
 * Subscribe to the caller's job feed, invoking `onJob` for every snapshot and
 * `onReady` once the initial replay of current jobs is complete, until the
 * connection ends or `signal` aborts. The feed seeds the current jobs on
 * connect (ended by the ready marker), so a reconnect re-converges on live
 * state while letting the caller tell the seed apart from later transitions.
 */
export async function subscribeJobs(
  onJob: (job: JobView) => void,
  onReady: () => void,
  onScopeChanged: (scope: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await checkedResponse(`${API_BASE_URL}/api/jobs/events`, "Job feed failed", {
    signal,
  });

  // The feed never terminates with a completion event, so `readSseEvents`
  // throws "Stream ended..." when the connection drops — the caller's
  // reconnect loop handles that as a normal reconnect signal.
  await readSseEvents(res, FeedEventSchema, (event) => {
    if (!("type" in event)) onJob(event);
    else if (event.type === "ready") onReady();
    else onScopeChanged(event.scope);
    return undefined;
  });
}

// --- Bulk operation API functions ---
//
// Each bulk operation runs as one background job: the POST resolves with the
// job's initial snapshot and per-file progress then arrives through the `/jobs`
// feed, just like single uploads and reconverts.

/** Bulk rechunk multiple documents as a background job. */
export function bulkRechunk(files: string[], spec?: PipelineSpec): Promise<JobView> {
  return postJob(
    `${API_BASE_URL}/api/documents/rechunk/bulk`,
    { files, pipeline: spec ?? {} },
    "Bulk rechunk failed",
  );
}

/** Bulk reconvert multiple documents as a background job. */
export function bulkReconvert(
  files: string[],
  spec?: PipelineSpec,
  llm?: LlmConfig,
): Promise<JobView> {
  return postJob(
    `${API_BASE_URL}/api/documents/reconvert/bulk`,
    { files, pipeline: spec ?? {}, llm: llm ?? {} },
    "Bulk reconvert failed",
  );
}

export interface BulkMoveEntry {
  source: string;
  destination: string;
}

/** Bulk move multiple documents as a background job. */
export function bulkMove(moves: BulkMoveEntry[]): Promise<JobView> {
  return postJob(`${API_BASE_URL}/api/documents/move/bulk`, { moves }, "Bulk move failed");
}

/** Bulk delete multiple documents as a background job. */
export function bulkDelete(files: string[]): Promise<JobView> {
  return postJob(`${API_BASE_URL}/api/documents/delete/bulk`, { files }, "Bulk delete failed");
}

// User directory management

/** Fetch a workspace directory tree; `scope` is `~` (personal) or `@<group>`. */
export async function getDirectories(scope: string): Promise<DirectoryTreeResponse> {
  return requestJson(
    `${API_BASE_URL}/api/directories/${encodeFilePath(scope)}`,
    "Failed to fetch directory tree",
    DirectoryTreeResponseSchema,
  );
}

export async function createDirectory(path: string): Promise<void> {
  return requestVoid(
    `${API_BASE_URL}/api/directories`,
    "Failed to create directory",
    jsonRequest("POST", { path }),
  );
}

export async function deleteDirectory(dirpath: string): Promise<void> {
  return requestVoid(
    `${API_BASE_URL}/api/directories`,
    "Failed to delete directory",
    jsonRequest("DELETE", { path: dirpath }),
  );
}

export async function moveDirectory(source: string, destination: string): Promise<void> {
  return requestVoid(
    `${API_BASE_URL}/api/directories/move`,
    "Failed to move directory",
    jsonRequest("POST", { source, destination }),
  );
}

export async function moveDocument(filepath: string, destination: string): Promise<void> {
  return requestVoid(
    `${API_BASE_URL}/api/documents/move/${encodeFilePath(filepath)}`,
    "Move failed",
    jsonRequest("POST", { destination }),
  );
}

// ============================================================
// Bulk delete API functions
// ============================================================

/** Delete all conversations for the authenticated user. */
export async function deleteAllConversations(): Promise<void> {
  return requestVoid(`${API_BASE_URL}/api/conversations`, "Failed to delete conversations", {
    method: "DELETE",
  });
}

/**
 * Delete all documents, chunks, originals, and the search index in a workspace;
 * `scope` is `~` (personal) or `@<group>`.
 */
export async function deleteAllDocuments(scope: string): Promise<void> {
  return requestVoid(
    `${API_BASE_URL}/api/documents/${encodeFilePath(scope)}`,
    "Failed to delete documents",
    { method: "DELETE" },
  );
}

/** Clear the user's persistent memory. */
export async function clearMemory(): Promise<void> {
  return requestVoid(`${API_BASE_URL}/api/memory`, "Failed to clear memory", {
    method: "DELETE",
  });
}

/** Drop the scratch state agent runs parked in the user's workspaces. */
export async function clearScratch(): Promise<ScratchClearedResponse> {
  return requestJson(
    `${API_BASE_URL}/api/scratch`,
    "Failed to clear scratch files",
    ScratchClearedResponseSchema,
    { method: "DELETE" },
  );
}

/** Delete all user data (conversations, documents, tokens). */
export async function deleteAllUserData(): Promise<void> {
  return requestVoid(`${API_BASE_URL}/api/user-data`, "Failed to delete user data", {
    method: "DELETE",
  });
}

// ============================================================
// Admin API functions (require user.is_admin)
//
// All admin endpoints share the same response shapes: a generic
// `{action, message}` for single resets, a counter for reindex, and a
// list of actions for the composite factory reset.  Helpers stay thin
// so the UI can render confirmation dialogs and toast the message
// directly.
// ============================================================

/** POST helper that validates an admin reset response, raising on HTTP errors. */
async function adminPost<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  return requestJson(`${API_BASE_URL}${path}`, "Admin action failed", schema, { method: "POST" });
}

/** Wipe the workspace tree on disk and the matching SQL document rows. */
export function adminResetWorkspace(): Promise<AdminResetResponse> {
  return adminPost("/api/admin/reset/workspace", AdminResetResponseSchema);
}

/** Wipe every user, group, and the rows that cascade from them. */
export function adminResetDatabase(): Promise<AdminResetResponse> {
  return adminPost("/api/admin/reset/database", AdminResetResponseSchema);
}

/** Reconcile every casebase: prune workspace and SQL orphans. */
export function adminReindex(): Promise<AdminReindexResponse> {
  return adminPost("/api/admin/reindex", AdminReindexResponseSchema);
}

/** Composite factory reset: workspace + database. */
export function adminFactoryReset(): Promise<AdminFactoryResetResponse> {
  return adminPost("/api/admin/reset/factory", AdminFactoryResetResponseSchema);
}

/** List every user known to the local database (footprint-bearing only). */
export async function adminListUsers(): Promise<AdminUserInfo[]> {
  return (
    await requestJson(
      `${API_BASE_URL}/api/admin/users`,
      "Failed to list users",
      AdminListUsersResponseSchema,
    )
  ).users;
}

/** List every group known to the local database. */
export async function adminListGroups(): Promise<AdminGroupInfo[]> {
  return (
    await requestJson(
      `${API_BASE_URL}/api/admin/groups`,
      "Failed to list groups",
      AdminListGroupsResponseSchema,
    )
  ).groups;
}

/** Read the server's global maintenance flag. */
export async function adminGetMaintenance(): Promise<boolean> {
  return (
    await requestJson(
      `${API_BASE_URL}/api/admin/maintenance`,
      "Failed to read maintenance mode",
      AdminMaintenanceStateSchema,
    )
  ).enabled;
}

/** Set the server's global maintenance flag (persisted across restarts). */
export async function adminSetMaintenance(enabled: boolean): Promise<boolean> {
  return (
    await requestJson(
      `${API_BASE_URL}/api/admin/maintenance`,
      "Failed to set maintenance mode",
      AdminMaintenanceStateSchema,
      jsonRequest("PUT", { enabled }),
    )
  ).enabled;
}

/** Wipe all data owned by a single user (workspace + SQL + index). */
export async function adminDeleteUserData(userId: string): Promise<void> {
  return requestVoid(
    `${API_BASE_URL}/api/admin/users/${encodeURIComponent(userId)}/data`,
    "Failed to delete user data",
    { method: "DELETE" },
  );
}

/** Wipe all data owned by a single group (workspace + SQL + index). */
export async function adminDeleteGroupData(groupId: string): Promise<void> {
  return requestVoid(
    `${API_BASE_URL}/api/admin/groups/${encodeURIComponent(groupId)}/data`,
    "Failed to delete group data",
    { method: "DELETE" },
  );
}
