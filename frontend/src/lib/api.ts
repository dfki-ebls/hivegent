import type { UIMessage } from "@ai-sdk/react";
import { z } from "zod";

import type { AgentMode, McpServerEntry, ToolsSpec } from "./types";
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
  type ChunkingPipelineInfo,
  ChunkingPipelineInfoSchema,
  type CompactConversationResponse,
  CompactConversationResponseSchema,
  ConversationListResponseSchema,
  type ConversationSummary,
  ConversationSummarySchema,
  type ConversionPipelineInfo,
  ConversionPipelineInfoSchema,
  type CreateDirectoryResponse,
  CreateDirectoryResponseSchema,
  type DeleteDirectoryResponse,
  DeleteDirectoryResponseSchema,
  type DirectoryTreeResponse,
  DirectoryTreeResponseSchema,
  type MoveDirectoryResponse,
  MoveDirectoryResponseSchema,
  type GenerateTitleResponse,
  GenerateTitleResponseSchema,
  type JobView,
  JobViewSchema,
  type LlmConfig,
  type MoveDocumentResponse,
  type ToolInfo,
  ToolInfoSchema,
  type ToolRunResult,
  ToolRunResultSchema,
  type ToolSchema,
  ToolSchemaSchema,
  TranscriptionResponseSchema,
  MoveDocumentResponseSchema,
  type PipelineSpec,
} from "./types";

import { featureFlags } from "./feature-flags";

import { getImpersonation, IMPERSONATE_HEADER } from "@/lib/impersonation";
import { getOidc } from "@/oidc";

export const API_BASE_URL = import.meta.env.VITE_API_URL ?? "";

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
 * GET a JSON endpoint, including the HTTP status in the thrown error so a
 * failure report pinpoints the layer (backend status vs proxy 502/503).
 */
async function getJson(url: string, errorMsg: string): Promise<unknown> {
  const res = await authFetch(url);
  if (!res.ok) {
    await throwIfMaintenance(res);
    throw new Error(`${errorMsg} (HTTP ${res.status})`);
  }
  return (await res.json()) as unknown;
}

/**
 * Thrown when the backend rejects a request with the maintenance gate
 * (503 + `{"code": "maintenance"}` detail). The gate covers the whole
 * `/api` router, so any {@link getJson} reader can hit it; the settings
 * store turns it into the full-screen maintenance notice.
 */
export class MaintenanceError extends Error {}

/** Raise {@link MaintenanceError} if `res` is the maintenance gate's 503. */
async function throwIfMaintenance(res: Response): Promise<void> {
  if (res.status !== 503) return;
  const body = (await res.json().catch(() => null)) as {
    detail?: { code?: string; message?: string };
  } | null;
  if (body?.detail?.code === "maintenance") {
    throw new MaintenanceError(body.detail.message);
  }
}

/**
 * Build the error for a failed response: the backend's `detail` when the
 * body carries one, otherwise `fallback` suffixed with the HTTP status.
 * Proxy-level failures (e.g. a 502 for an upload that tripped a gateway
 * timeout) have no JSON body, and a bare fallback would hide the only
 * diagnostic available.
 */
async function responseError(res: Response, fallback: string): Promise<Error> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  return new Error(
    typeof body?.detail === "string" && body.detail
      ? body.detail
      : `${fallback} (HTTP ${res.status})`,
  );
}

/**
 * Validate a job-submission response into a {@link JobView}, raising `errorMsg`
 * on an HTTP error. Shared by every endpoint that starts a background job —
 * uploads (multipart) and the JSON job posts via {@link postJob}.
 */
async function parseJobResponse(res: Response, errorMsg: string): Promise<JobView> {
  if (!res.ok) {
    throw await responseError(res, errorMsg);
  }
  return JobViewSchema.parse(await res.json());
}

/** POST a JSON body to a job endpoint and return the started job's snapshot. */
async function postJob(url: string, body: unknown, errorMsg: string): Promise<JobView> {
  const res = await authFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return parseJobResponse(res, errorMsg);
}

/**
 * Get the current auth headers for use with external transports.
 */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {};
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

/** Canonical scope of a group's workspace. */
export function groupScope(groupId: string): string {
  return `@${groupId}`;
}

/**
 * Compose the canonical path of a document from its scope and local path
 * (e.g. `~/notes.md`, `@research/notes.md`).
 */
export function canonicalPath(scope: string, local: string): string {
  return `${scope}/${local}`;
}

/** Check if a file requires conversion (anything that is not already markdown). */
export function requiresConversion(filename: string): boolean {
  const ext = `.${filename.split(".").pop()?.toLowerCase() ?? ""}`;
  return ext !== ".md";
}

/** Probe the public readiness endpoint, aborting after `timeoutMs` so
 * polling callers don't stack up hung connections. */
export async function checkHealth(timeoutMs = 3000): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE_URL}/api/health`, {
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
 * Resolve once the backend reports healthy, polling `/api/health` until then.
 *
 * The probe runs once per app lifetime — the promise is cached and shared by
 * every caller — so startup fetches such as settings can gate on readiness
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

/** Fetch server-side LLM settings. */
export async function getSettings(): Promise<BackendSettings> {
  const data = await getJson(`${API_BASE_URL}/api/settings`, "Failed to fetch settings");
  return BackendSettingsSchema.parse(data);
}

/** Transcribe recorded audio via the backend STT endpoint. */
export async function transcribeAudio(audio: Blob): Promise<string> {
  const form = new FormData();
  form.append("audio", audio, "recording.webm");
  const res = await authFetch(`${API_BASE_URL}/api/transcription`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    throw new Error("Failed to transcribe audio");
  }
  const data: unknown = await res.json();
  return TranscriptionResponseSchema.parse(data).text;
}

/** Fetch available agent tools from the backend. */
export async function listTools(): Promise<ToolInfo[]> {
  const data = await getJson(`${API_BASE_URL}/api/tools`, "Failed to fetch tools");
  return z.array(ToolInfoSchema).parse(data);
}

/** Fetch every agent tool with its parameter JSON Schema (admin only). */
export async function listToolSchemas(): Promise<ToolSchema[]> {
  const data = await getJson(`${API_BASE_URL}/api/debug/tools`, "Failed to fetch tool schemas");
  return z.array(ToolSchemaSchema).parse(data);
}

/** Invoke an agent tool with arbitrary arguments (admin only). */
export async function runTool(name: string, args: Record<string, unknown>): Promise<ToolRunResult> {
  const res = await authFetch(`${API_BASE_URL}/api/debug/tools/${encodeURIComponent(name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!res.ok) {
    throw new Error(`Failed to run tool (${res.status})`);
  }
  return ToolRunResultSchema.parse(await res.json());
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
 * Convert a frontend ToolsSpec to the snake_case backend payload.
 *
 * When the {@link featureFlags.toolsSpec} flag is disabled, returns an
 * empty payload regardless of the stored spec — tool/MCP customization is
 * a frontend-only feature, so disabling it implicitly removes the data
 * from every outgoing request without touching the backend.
 */
/**
 * Build the agent mode value sent to the backend.
 *
 * When the {@link featureFlags.planning} flag is disabled, always returns
 * `"execute"` — plan mode is a frontend-only feature, so disabling it
 * implicitly prevents the backend from ever appending the plan instructions
 * without a parallel flag system.
 */
export function buildModePayload(mode: AgentMode): AgentMode {
  if (!featureFlags.planning) return "execute";
  return mode;
}

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

export async function getConversationMessages(conversationId: string): Promise<UIMessage[]> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`);
  if (!res.ok) {
    return [];
  }
  const data: unknown = await res.json();
  if (!Array.isArray(data)) return [];
  return data as UIMessage[];
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
  const res = await authFetch(`${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`, {
    method: "PUT",
    body: documentUploadFormData(filename, file, options),
    signal: options?.signal,
  });

  return parseJobResponse(res, "Upload failed");
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
  const res = await authFetch(`${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, mode, chunking: chunking ?? null }),
  });

  if (!res.ok) {
    throw await responseError(res, "Save failed");
  }
}

/** Options for collection upload (ZIP or directory). */
export interface UploadCollectionOptions {
  spec?: PipelineSpec;
  llm?: LlmConfig;
}

/**
 * Upload a collection ZIP and start its background job; `scope` is `~`
 * (personal) or `@<group>`. Resolves with the job's initial snapshot; per-file
 * progress then arrives through the `/jobs` feed.
 */
export async function uploadCollection(
  scope: string,
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

  const res = await authFetch(
    `${API_BASE_URL}/api/documents/collections/${encodeFilePath(scope)}`,
    { method: "POST", body: formData, signal: options?.signal },
  );

  return parseJobResponse(res, "Collection upload failed");
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
  const res = await authFetch(`${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`, {
    method: "DELETE",
  });

  if (!res.ok) {
    throw await responseError(res, "Delete failed");
  }
}

export async function getDocumentContent(filename: string): Promise<string> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`);

  if (!res.ok) {
    throw new Error("Failed to fetch document content");
  }

  return res.text();
}

/** Fetch a workspace asset (e.g. image) as a blob URL for display. */
export async function fetchDocumentAsset(filepath: string, signal?: AbortSignal): Promise<string> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/${encodeFilePath(filepath)}`, {
    signal,
  });

  if (!res.ok) {
    throw new Error("Failed to fetch document asset");
  }

  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

/** Download the original binary file for a document. */
export async function downloadOriginal(filepath: string): Promise<Blob> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/original/${encodeFilePath(filepath)}`);

  if (!res.ok) {
    throw await responseError(res, "Download failed");
  }

  return res.blob();
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

  const res = await authFetch(
    `${API_BASE_URL}/api/documents/original/${encodeFilePath(filepath)}`,
    { method: "PUT", body: formData },
  );

  return parseJobResponse(res, "Replace failed");
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const data = await getJson(`${API_BASE_URL}/api/conversations`, "Failed to list conversations");
  return ConversationListResponseSchema.parse(data).conversations;
}

export async function updateConversationTitle(
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });

  if (!res.ok) {
    throw await responseError(res, "Update failed");
  }

  const data: unknown = await res.json();
  return ConversationSummarySchema.parse(data);
}

export async function generateConversationTitle(
  conversationId: string,
  llm: LlmConfig,
): Promise<GenerateTitleResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversations/${conversationId}/title/generation`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm }),
    },
  );

  if (!res.ok) {
    throw await responseError(res, "Title generation failed");
  }

  const data: unknown = await res.json();
  return GenerateTitleResponseSchema.parse(data);
}

export async function compactConversation(
  conversationId: string,
  llm: LlmConfig,
  messages: UIMessage[],
): Promise<CompactConversationResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/compaction`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ llm, messages }),
  });

  if (!res.ok) {
    throw await responseError(res, "Compaction failed");
  }

  const data: unknown = await res.json();
  return CompactConversationResponseSchema.parse(data);
}

export async function getConversation(conversationId: string): Promise<ConversationSummary | null> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`);
  if (!res.ok) return null;
  const data: unknown = await res.json();
  return ConversationSummarySchema.parse(data);
}

/** Download a conversation's raw database payloads as a JSON blob. */
export async function exportConversation(conversationId: string): Promise<Blob> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/export`);
  if (!res.ok) {
    throw await responseError(res, "Export failed");
  }
  return res.blob();
}

/** Restore a previously exported conversation as a new chat owned by the user. */
export async function importConversation(file: File): Promise<ConversationSummary> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await file.text(),
  });

  if (!res.ok) {
    throw await responseError(res, "Import failed");
  }

  const data: unknown = await res.json();
  return ConversationSummarySchema.parse(data);
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
    method: "DELETE",
  });

  if (!res.ok) {
    throw await responseError(res, "Delete failed");
  }
}

// Conversion pipeline API functions

export async function listConversionPipelines(): Promise<ConversionPipelineInfo[]> {
  const data = await getJson(
    `${API_BASE_URL}/api/pipelines/conversion`,
    "Failed to get conversion pipelines",
  );
  return z.array(ConversionPipelineInfoSchema).parse(data);
}

// Chunking pipeline API functions

export async function listChunkingPipelines(): Promise<ChunkingPipelineInfo[]> {
  const data = await getJson(
    `${API_BASE_URL}/api/pipelines/chunking`,
    "Failed to get chunking pipelines",
  );
  return z.array(ChunkingPipelineInfoSchema).parse(data);
}

export async function getDocumentChunks(filename: string): Promise<ChunkedDocumentResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/chunks/${encodeFilePath(filename)}`);

  if (!res.ok) {
    throw await responseError(res, "Failed to fetch chunks");
  }

  const data: unknown = await res.json();
  return ChunkedDocumentResponseSchema.parse(data);
}

// Asset API functions

/** List assets for a document. */
export async function listDocumentAssets(filename: string): Promise<AssetListResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/assets/${encodeFilePath(filename)}`);
  if (!res.ok) {
    throw await responseError(res, "Failed to list assets");
  }
  const data: unknown = await res.json();
  return AssetListResponseSchema.parse(data);
}

/** Update an asset's companion .md description. */
export async function updateAssetDescription(
  filename: string,
  assetName: string,
  content: string,
): Promise<AssetEntry> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/assets/${encodeFilePath(filename)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_name: assetName, content }),
  });
  if (!res.ok) {
    throw await responseError(res, "Failed to update description");
  }
  const data: unknown = await res.json();
  return AssetEntrySchema.parse(data);
}

/** Generate an asset's companion .md description with the vision model. */
export async function generateAssetDescription(
  filename: string,
  assetName: string,
  llm?: LlmConfig,
): Promise<AssetEntry> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/assets/${encodeFilePath(filename)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_name: assetName, llm: llm ?? {} }),
  });
  if (!res.ok) {
    throw await responseError(res, "Failed to generate description");
  }
  const data: unknown = await res.json();
  return AssetEntrySchema.parse(data);
}

/** Delete an asset's companion .md description, keeping the asset itself. */
export async function deleteAssetDescription(
  filename: string,
  assetName: string,
): Promise<AssetEntry> {
  const res = await authFetch(
    `${API_BASE_URL}/api/documents/assets/${encodeFilePath(filename)}?asset_name=${encodeURIComponent(assetName)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw await responseError(res, "Failed to delete description");
  }
  const data: unknown = await res.json();
  return AssetEntrySchema.parse(data);
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
  const res = await authFetch(
    `${API_BASE_URL}/api/documents/reconvert/${encodeFilePath(filename)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pipeline: options?.spec ?? {}, llm: options?.llm ?? {} }),
      signal: options?.signal,
    },
  );

  return parseJobResponse(res, "Reconvert failed");
}

// --- Background jobs (generic) ---

/** List the caller's known background jobs. */
export async function listJobs(): Promise<JobView[]> {
  const res = await authFetch(`${API_BASE_URL}/api/jobs`);
  if (!res.ok) {
    throw await responseError(res, "Failed to list jobs");
  }
  return z.array(JobViewSchema).parse(await res.json());
}

/** Request cancellation of a background job. Idempotent server-side. */
export async function cancelJob(id: string): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/jobs/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw await responseError(res, "Failed to cancel job");
  }
}

/**
 * Subscribe to the caller's job feed, invoking `onJob` for every snapshot
 * until the connection ends or `signal` aborts. The feed seeds the current
 * jobs on connect, so a reconnect re-converges on live state.
 */
export async function subscribeJobs(
  onJob: (job: JobView) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/jobs/events`, { signal });
  if (!res.ok) {
    throw new Error(`Job feed failed (HTTP ${res.status})`);
  }

  // The feed never terminates with a completion event, so `readSseEvents`
  // throws "Stream ended..." when the connection drops — the caller's
  // reconnect loop handles that as a normal reconnect signal.
  await readSseEvents(res, JobViewSchema, (job) => {
    onJob(job);
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
  const data = await getJson(
    `${API_BASE_URL}/api/directories/${encodeFilePath(scope)}`,
    "Failed to fetch directory tree",
  );
  return DirectoryTreeResponseSchema.parse(data);
}

export async function createDirectory(path: string): Promise<CreateDirectoryResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/directories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });

  if (!res.ok) {
    throw await responseError(res, "Failed to create directory");
  }

  const data: unknown = await res.json();
  return CreateDirectoryResponseSchema.parse(data);
}

export async function deleteDirectory(dirpath: string): Promise<DeleteDirectoryResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/directories`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: dirpath }),
  });

  if (!res.ok) {
    throw await responseError(res, "Failed to delete directory");
  }

  const data: unknown = await res.json();
  return DeleteDirectoryResponseSchema.parse(data);
}

export async function moveDirectory(
  source: string,
  destination: string,
): Promise<MoveDirectoryResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/directories/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, destination }),
  });

  if (!res.ok) {
    throw await responseError(res, "Failed to move directory");
  }

  const data: unknown = await res.json();
  return MoveDirectoryResponseSchema.parse(data);
}

export async function moveDocument(
  filepath: string,
  destination: string,
): Promise<MoveDocumentResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/move/${encodeFilePath(filepath)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ destination }),
  });

  if (!res.ok) {
    throw await responseError(res, "Move failed");
  }

  const data: unknown = await res.json();
  return MoveDocumentResponseSchema.parse(data);
}

// ============================================================
// Bulk delete API functions
// ============================================================

/** Delete all conversations for the authenticated user. */
export async function deleteAllConversations(): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw await responseError(res, "Failed to delete conversations");
  }
}

/**
 * Delete all documents, chunks, originals, and the search index in a workspace;
 * `scope` is `~` (personal) or `@<group>`.
 */
export async function deleteAllDocuments(scope: string): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/${encodeFilePath(scope)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw await responseError(res, "Failed to delete documents");
  }
}

/** Clear the user's persistent memory. */
export async function clearMemory(): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/memory`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw await responseError(res, "Failed to clear memory");
  }
}

/** Delete all user data (conversations, documents, tokens). */
export async function deleteAllUserData(): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/user-data`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw await responseError(res, "Failed to delete user data");
  }
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
  const res = await authFetch(`${API_BASE_URL}${path}`, { method: "POST" });
  if (!res.ok) {
    throw await responseError(res, "Admin action failed");
  }
  return schema.parse(await res.json());
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
  const data = await getJson(`${API_BASE_URL}/api/admin/users`, "Failed to list users");
  return AdminListUsersResponseSchema.parse(data).users;
}

/** List every group known to the local database. */
export async function adminListGroups(): Promise<AdminGroupInfo[]> {
  const data = await getJson(`${API_BASE_URL}/api/admin/groups`, "Failed to list groups");
  return AdminListGroupsResponseSchema.parse(data).groups;
}

/** Read the server's global maintenance flag. */
export async function adminGetMaintenance(): Promise<boolean> {
  const data = await getJson(
    `${API_BASE_URL}/api/admin/maintenance`,
    "Failed to read maintenance mode",
  );
  return AdminMaintenanceStateSchema.parse(data).enabled;
}

/** Set the server's global maintenance flag (persisted across restarts). */
export async function adminSetMaintenance(enabled: boolean): Promise<boolean> {
  const res = await authFetch(`${API_BASE_URL}/api/admin/maintenance`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) {
    throw await responseError(res, "Failed to set maintenance mode");
  }
  return AdminMaintenanceStateSchema.parse(await res.json()).enabled;
}

/** Wipe all data owned by a single user (workspace + SQL + index). */
export async function adminDeleteUserData(userId: string): Promise<void> {
  const res = await authFetch(
    `${API_BASE_URL}/api/admin/users/${encodeURIComponent(userId)}/data`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw await responseError(res, "Failed to delete user data");
  }
}

/** Wipe all data owned by a single group (workspace + SQL + index). */
export async function adminDeleteGroupData(groupId: string): Promise<void> {
  const res = await authFetch(
    `${API_BASE_URL}/api/admin/groups/${encodeURIComponent(groupId)}/data`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    throw await responseError(res, "Failed to delete group data");
  }
}
