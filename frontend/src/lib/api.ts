import type { UIMessage } from "@ai-sdk/react";
import { z } from "zod";

import type {
  AgentMode,
  BulkOperationCompleteEvent,
  BulkOperationStreamEvent,
  CollectionStreamEvent,
  McpServerEntry,
  OperationStage,
  ToolsSpec,
  UploadProgress,
} from "./types";
import {
  type AdminFactoryResetResponse,
  AdminFactoryResetResponseSchema,
  type AdminGroupInfo,
  AdminListGroupsResponseSchema,
  AdminListUsersResponseSchema,
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
  BulkOperationStreamEventSchema,
  type ChunkedDocumentResponse,
  ChunkedDocumentResponseSchema,
  type ChunkingPipelineInfo,
  ChunkingPipelineInfoSchema,
  type CollectionUploadResponse,
  CollectionUploadResponseSchema,
  CollectionStreamEventSchema,
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
  type DocumentInfo,
  DocumentListResponseSchema,
  type GenerateTitleResponse,
  GenerateTitleResponseSchema,
  type LlmConfig,
  type MoveDocumentResponse,
  type ToolInfo,
  ToolInfoSchema,
  MoveDocumentResponseSchema,
  type PipelineSpec,
  type RechunkCompleteEvent,
  RechunkStreamEventSchema,
  type UploadDocumentResponse,
  UploadDocumentResponseSchema,
  UploadStreamEventSchema,
} from "./types";

import { featureFlags } from "./feature-flags";

import { getOidc } from "@/oidc";

export const API_BASE_URL = import.meta.env.VITE_API_URL ?? "";

/**
 * Make an authenticated fetch request.
 */
async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);

  for (const [key, value] of Object.entries(await getAuthHeaders())) {
    headers.set(key, value);
  }

  return fetch(url, { ...options, headers });
}

/**
 * Get the current auth headers for use with external transports.
 */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  const oidc = await getOidc();
  if (oidc.isUserLoggedIn) {
    const token = await oidc.getAccessToken();
    return { Authorization: `Bearer ${token}` };
  }
  return {};
}

/**
 * Encode a file path for use in URLs.
 * Encodes each segment individually so that `/` separators are preserved.
 */
function encodeFilePath(filepath: string): string {
  return filepath.split("/").map(encodeURIComponent).join("/");
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

/** Fetch server-side LLM settings. */
export async function getSettings(): Promise<BackendSettings> {
  const res = await authFetch(`${API_BASE_URL}/api/settings`);
  if (!res.ok) {
    throw new Error("Failed to fetch settings");
  }
  const data: unknown = await res.json();
  return BackendSettingsSchema.parse(data);
}

/** Fetch available agent tools from the backend. */
export async function listTools(): Promise<ToolInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/tools`);
  if (!res.ok) {
    throw new Error("Failed to fetch tools");
  }
  const data: unknown = await res.json();
  return z.array(ToolInfoSchema).parse(data);
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

/** LLM config for auxiliary tasks (titles, compaction): prefer aux model, fall back to primary. */
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

export async function listDocuments(): Promise<DocumentInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/documents`);
  if (!res.ok) {
    throw new Error("Failed to list documents");
  }
  const data: unknown = await res.json();
  return DocumentListResponseSchema.parse(data).documents;
}

/** Options for document upload. */
export interface UploadDocumentOptions {
  spec?: PipelineSpec;
  llm?: LlmConfig;
  targetDirectory?: string;
  overwrite?: boolean;
}

export async function uploadDocument(
  filename: string,
  file: File,
  options?: UploadDocumentOptions,
): Promise<UploadDocumentResponse> {
  const formData = new FormData();
  formData.append("file", file);

  // Build the filepath, optionally prepending the target directory
  const filepath = options?.targetDirectory ? `${options.targetDirectory}/${filename}` : filename;

  const url = `${API_BASE_URL}/api/documents/${encodeFilePath(filepath)}`;

  if (options?.overwrite) {
    formData.append("overwrite", "true");
  }
  if (options?.spec) {
    formData.append("pipeline_spec", JSON.stringify(options.spec));
  }
  if (requiresConversion(filename) && options?.llm) {
    formData.append("llm_config", JSON.stringify(options.llm));
  }

  const res = await authFetch(url, {
    method: "PUT",
    body: formData,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(error.detail || "Upload failed");
  }

  const data: unknown = await res.json();
  return UploadDocumentResponseSchema.parse(data);
}

/** Options for collection upload (ZIP or directory). */
export interface UploadCollectionOptions {
  spec?: PipelineSpec;
  llm?: LlmConfig;
}

/** Build FormData for a collection upload (shared by streaming and non-streaming paths). */
function buildCollectionFormData(file: File, options?: UploadCollectionOptions): FormData {
  const formData = new FormData();
  formData.append("file", file);
  if (options?.spec) {
    formData.append("pipeline_spec", JSON.stringify(options.spec));
  }
  if (options?.llm) {
    formData.append("llm_config", JSON.stringify(options.llm));
  }
  return formData;
}

/** Upload a markdown collection as a ZIP archive. */
export async function uploadCollection(
  file: File,
  options?: UploadCollectionOptions,
): Promise<CollectionUploadResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/collections`, {
    method: "POST",
    body: buildCollectionFormData(file, options),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Collection upload failed" }));
    throw new Error(error.detail || "Collection upload failed");
  }

  const data: unknown = await res.json();
  return CollectionUploadResponseSchema.parse(data);
}

/** Options for streaming collection upload (extends base with progress + abort). */
export type StreamingCollectionOptions = UploadCollectionOptions & {
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
};

/** Post a collection ZIP to a streaming endpoint and parse SSE progress events. */
async function postCollectionStream(
  url: string,
  file: File,
  options?: StreamingCollectionOptions,
): Promise<CollectionUploadResponse> {
  const formData = buildCollectionFormData(file, options);

  const res = await authFetch(url, {
    method: "POST",
    body: formData,
    signal: options?.signal,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Collection upload failed" }));
    throw new Error(error.detail || "Collection upload failed");
  }

  return parseSseProgressStream<CollectionStreamEvent, CollectionUploadResponse>(
    res,
    CollectionStreamEventSchema,
    options?.onProgress,
  );
}

/** Upload a collection with streaming progress events via SSE. */
export function uploadCollectionStream(
  file: File,
  options?: StreamingCollectionOptions,
): Promise<CollectionUploadResponse> {
  return postCollectionStream(`${API_BASE_URL}/api/documents/collections/stream`, file, options);
}

/** Upload a collection to a group with streaming progress events via SSE. */
export function uploadGroupCollectionStream(
  groupId: string,
  file: File,
  options?: StreamingCollectionOptions,
): Promise<CollectionUploadResponse> {
  return postCollectionStream(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/documents/collections/stream`,
    file,
    options,
  );
}

/**
 * Shared SSE stream reader.  Reads `data:` lines from a streaming response,
 * validates each against `schema`, and calls `onEvent` for every parsed event.
 * Returns the value stored by `onEvent` via its return value (non-undefined
 * means "this is the final result").
 */
async function readSseEvents<TEvent extends { type: string }, TResult>(
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

/**
 * Generic SSE stream parser for progress + completion event protocols.
 *
 * Works with any discriminated union where progress events have
 * `type: "progress"` with `file`, `current`, `total`, `status` fields,
 * and completion events have `type: "complete"`.
 */
async function parseSseProgressStream<TEvent extends { type: string }, TComplete>(
  res: Response,
  schema: z.ZodType<TEvent>,
  onProgress?: (progress: UploadProgress) => void,
): Promise<TComplete> {
  const failedFiles: string[] = [];

  return readSseEvents(res, schema, (event) => {
    if (event.type === "progress") {
      const p = event as TEvent & {
        file: string;
        current: number;
        total: number;
        status: string;
      };
      if (p.status === "failed") {
        failedFiles.push(p.file);
      }
      onProgress?.({
        current: p.current,
        total: p.total,
        currentFile: p.file,
        failedFiles: [...failedFiles],
      });
      return undefined;
    }
    return event as unknown as TComplete;
  });
}

export async function deleteDocument(filename: string): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`, {
    method: "DELETE",
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Delete failed" }));
    throw new Error(error.detail || "Delete failed");
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
export async function fetchDocumentAsset(filepath: string): Promise<string> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/${encodeFilePath(filepath)}`);

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
    const error = await res.json().catch(() => ({ detail: "Download failed" }));
    throw new Error(error.detail || "Download failed");
  }

  return res.blob();
}

/** Replace the original binary file and reconvert the document. */
export async function replaceOriginal(
  filepath: string,
  file: File,
  spec?: PipelineSpec,
  llm?: LlmConfig,
): Promise<UploadDocumentResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (spec) formData.append("pipeline_spec", JSON.stringify(spec));
  if (llm) formData.append("llm_config", JSON.stringify(llm));

  const res = await authFetch(
    `${API_BASE_URL}/api/documents/original/${encodeFilePath(filepath)}`,
    { method: "PUT", body: formData },
  );

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Replace failed" }));
    throw new Error(error.detail || "Replace failed");
  }

  const data: unknown = await res.json();
  return UploadDocumentResponseSchema.parse(data);
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations`);
  if (!res.ok) {
    throw new Error("Failed to list conversations");
  }
  const data: unknown = await res.json();
  return ConversationListResponseSchema.parse(data).conversations;
}

export async function updateConversationTitle(
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/title`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Update failed" }));
    throw new Error(error.detail || "Update failed");
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
    const error = await res.json().catch(() => ({ detail: "Title generation failed" }));
    throw new Error(error.detail || "Title generation failed");
  }

  const data: unknown = await res.json();
  return GenerateTitleResponseSchema.parse(data);
}

export async function compactConversation(
  conversationId: string,
  llm: LlmConfig,
): Promise<CompactConversationResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}/compaction`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ llm }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Compaction failed" }));
    throw new Error(error.detail || "Compaction failed");
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

export async function deleteConversation(conversationId: string): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations/${conversationId}`, {
    method: "DELETE",
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Delete failed" }));
    throw new Error(error.detail || "Delete failed");
  }
}

// Conversion pipeline API functions

export async function listConversionPipelines(): Promise<ConversionPipelineInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/pipelines/conversion`);

  if (!res.ok) {
    throw new Error("Failed to get conversion pipelines");
  }

  const data: unknown = await res.json();
  return z.array(ConversionPipelineInfoSchema).parse(data);
}

// Chunking pipeline API functions

export async function listChunkingPipelines(): Promise<ChunkingPipelineInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/pipelines/chunking`);

  if (!res.ok) {
    throw new Error("Failed to get chunking pipelines");
  }

  const data: unknown = await res.json();
  return z.array(ChunkingPipelineInfoSchema).parse(data);
}

export async function getDocumentChunks(filename: string): Promise<ChunkedDocumentResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/chunks/${encodeFilePath(filename)}`);

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to fetch chunks" }));
    throw new Error(error.detail || "Failed to fetch chunks");
  }

  const data: unknown = await res.json();
  return ChunkedDocumentResponseSchema.parse(data);
}

// Asset API functions

/** List assets for a document. */
export async function listDocumentAssets(filename: string): Promise<AssetListResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/assets/${encodeFilePath(filename)}`);
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to list assets" }));
    throw new Error(error.detail || "Failed to list assets");
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
    const error = await res.json().catch(() => ({ detail: "Failed to update description" }));
    throw new Error(error.detail || "Failed to update description");
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
    const error = await res.json().catch(() => ({ detail: "Failed to generate description" }));
    throw new Error(error.detail || "Failed to generate description");
  }
  const data: unknown = await res.json();
  return AssetEntrySchema.parse(data);
}

/** List assets for a group document. */
export async function listGroupDocumentAssets(
  groupId: string,
  filename: string,
): Promise<AssetListResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/documents/assets/${encodeFilePath(filename)}`,
  );
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to list assets" }));
    throw new Error(error.detail || "Failed to list assets");
  }
  const data: unknown = await res.json();
  return AssetListResponseSchema.parse(data);
}

/** Update an asset's companion .md description in a group. */
export async function updateGroupAssetDescription(
  groupId: string,
  filename: string,
  assetName: string,
  content: string,
): Promise<AssetEntry> {
  const res = await authFetch(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/documents/assets/${encodeFilePath(filename)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_name: assetName, content }),
    },
  );
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to update description" }));
    throw new Error(error.detail || "Failed to update description");
  }
  const data: unknown = await res.json();
  return AssetEntrySchema.parse(data);
}

/** Generate an asset's companion .md description in a group. */
export async function generateGroupAssetDescription(
  groupId: string,
  filename: string,
  assetName: string,
  llm?: LlmConfig,
): Promise<AssetEntry> {
  const res = await authFetch(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/documents/assets/${encodeFilePath(filename)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset_name: assetName, llm: llm ?? {} }),
    },
  );
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to generate description" }));
    throw new Error(error.detail || "Failed to generate description");
  }
  const data: unknown = await res.json();
  return AssetEntrySchema.parse(data);
}

/** Options for document reconversion. */
export interface ReconvertDocumentOptions {
  spec?: PipelineSpec;
  llm?: LlmConfig;
}

export async function reconvertDocument(
  filename: string,
  options?: ReconvertDocumentOptions,
): Promise<UploadDocumentResponse> {
  const url = `${API_BASE_URL}/api/documents/reconvert/${encodeFilePath(filename)}`;

  const res = await authFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pipeline: options?.spec ?? {},
      llm: options?.llm ?? {},
    }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Reconvert failed" }));
    throw new Error(error.detail || "Reconvert failed");
  }

  const data: unknown = await res.json();
  return UploadDocumentResponseSchema.parse(data);
}

export async function rechunkDocument(
  filename: string,
  spec?: PipelineSpec,
): Promise<ChunkedDocumentResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/rechunk/${encodeFilePath(filename)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec ?? {}),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Rechunk failed" }));
    throw new Error(error.detail || "Rechunk failed");
  }

  const data: unknown = await res.json();
  return ChunkedDocumentResponseSchema.parse(data);
}

// --- SSE stage-based stream parser ---

/**
 * Parse an SSE stream that emits stage, error, and complete events.
 * Calls `onStage` for each stage event, throws on error events,
 * and returns the complete event payload.
 */
async function parseSseStageStream<TComplete>(
  res: Response,
  schema: z.ZodType<{ type: string }>,
  onStage?: (stage: OperationStage) => void,
): Promise<TComplete> {
  return readSseEvents(res, schema, (event) => {
    if (event.type === "stage") {
      const s = event as { type: "stage"; stage: string; detail: string };
      onStage?.({ stage: s.stage, detail: s.detail });
      return undefined;
    }
    if (event.type === "error") {
      const e = event as { type: "error"; detail: string };
      throw new Error(e.detail);
    }
    return event as unknown as TComplete;
  });
}

/** Options for streaming single-document operations. */
export interface StreamingOperationOptions {
  onStage?: (stage: OperationStage) => void;
  signal?: AbortSignal;
}

/** Upload a document with streaming progress events via SSE. */
export async function uploadDocumentStream(
  filename: string,
  file: File,
  options?: UploadDocumentOptions & StreamingOperationOptions,
): Promise<UploadDocumentResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const filepath = options?.targetDirectory ? `${options.targetDirectory}/${filename}` : filename;
  const url = `${API_BASE_URL}/api/documents/stream/${encodeFilePath(filepath)}`;

  if (options?.overwrite) {
    formData.append("overwrite", "true");
  }
  if (options?.spec) {
    formData.append("pipeline_spec", JSON.stringify(options.spec));
  }
  if (requiresConversion(filename) && options?.llm) {
    formData.append("llm_config", JSON.stringify(options.llm));
  }

  const res = await authFetch(url, {
    method: "PUT",
    body: formData,
    signal: options?.signal,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(error.detail || "Upload failed");
  }

  return parseSseStageStream<UploadDocumentResponse>(
    res,
    UploadStreamEventSchema,
    options?.onStage,
  );
}

/** Reconvert a document with streaming progress events via SSE. */
export async function reconvertDocumentStream(
  filename: string,
  options?: ReconvertDocumentOptions & StreamingOperationOptions,
): Promise<UploadDocumentResponse> {
  const url = `${API_BASE_URL}/api/documents/reconvert/stream/${encodeFilePath(filename)}`;

  const res = await authFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pipeline: options?.spec ?? {},
      llm: options?.llm ?? {},
    }),
    signal: options?.signal,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Reconvert failed" }));
    throw new Error(error.detail || "Reconvert failed");
  }

  return parseSseStageStream<UploadDocumentResponse>(
    res,
    UploadStreamEventSchema,
    options?.onStage,
  );
}

/** Rechunk a document with streaming progress events via SSE. */
export async function rechunkDocumentStream(
  filename: string,
  spec?: PipelineSpec,
  options?: StreamingOperationOptions,
): Promise<RechunkCompleteEvent> {
  const url = `${API_BASE_URL}/api/documents/rechunk/stream/${encodeFilePath(filename)}`;

  const res = await authFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec ?? {}),
    signal: options?.signal,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Rechunk failed" }));
    throw new Error(error.detail || "Rechunk failed");
  }

  return parseSseStageStream<RechunkCompleteEvent>(res, RechunkStreamEventSchema, options?.onStage);
}

// --- Bulk operation API functions ---

/** Options for streaming bulk operations. */
export interface BulkOperationStreamOptions {
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

/** Bulk rechunk multiple documents with streaming progress. */
export async function bulkRechunkStream(
  files: string[],
  spec?: PipelineSpec,
  options?: BulkOperationStreamOptions,
): Promise<BulkOperationCompleteEvent> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/rechunk/bulk/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files, pipeline: spec ?? {} }),
    signal: options?.signal,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Bulk rechunk failed" }));
    throw new Error(error.detail || "Bulk rechunk failed");
  }

  return parseSseProgressStream<BulkOperationStreamEvent, BulkOperationCompleteEvent>(
    res,
    BulkOperationStreamEventSchema,
    options?.onProgress,
  );
}

/** Bulk reconvert multiple documents with streaming progress. */
export async function bulkReconvertStream(
  files: string[],
  spec?: PipelineSpec,
  llm?: LlmConfig,
  options?: BulkOperationStreamOptions,
): Promise<BulkOperationCompleteEvent> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/reconvert/bulk/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files, pipeline: spec ?? {}, llm: llm ?? {} }),
    signal: options?.signal,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Bulk reconvert failed" }));
    throw new Error(error.detail || "Bulk reconvert failed");
  }

  return parseSseProgressStream<BulkOperationStreamEvent, BulkOperationCompleteEvent>(
    res,
    BulkOperationStreamEventSchema,
    options?.onProgress,
  );
}

/** Bulk delete multiple documents with streaming progress. */
export async function bulkDeleteStream(
  files: string[],
  options?: BulkOperationStreamOptions,
): Promise<BulkOperationCompleteEvent> {
  const res = await authFetch(`${API_BASE_URL}/api/documents/delete/bulk/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files }),
    signal: options?.signal,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Bulk delete failed" }));
    throw new Error(error.detail || "Bulk delete failed");
  }

  return parseSseProgressStream<BulkOperationStreamEvent, BulkOperationCompleteEvent>(
    res,
    BulkOperationStreamEventSchema,
    options?.onProgress,
  );
}

// User directory management

export async function getDirectories(): Promise<DirectoryTreeResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/directories`);
  if (!res.ok) {
    throw new Error("Failed to fetch directory tree");
  }
  const data: unknown = await res.json();
  return DirectoryTreeResponseSchema.parse(data);
}

export async function createDirectory(path: string): Promise<CreateDirectoryResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/directories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to create directory" }));
    throw new Error(error.detail || "Failed to create directory");
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
    const error = await res.json().catch(() => ({ detail: "Failed to delete directory" }));
    throw new Error(error.detail || "Failed to delete directory");
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
    const error = await res.json().catch(() => ({ detail: "Failed to move directory" }));
    throw new Error(error.detail || "Failed to move directory");
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
    const error = await res.json().catch(() => ({ detail: "Move failed" }));
    throw new Error(error.detail || "Move failed");
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
    const error = await res.json().catch(() => ({ detail: "Failed to delete conversations" }));
    throw new Error(error.detail || "Failed to delete conversations");
  }
}

/** Delete all documents, chunks, originals, and the search index. */
export async function deleteAllDocuments(): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/documents`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to delete documents" }));
    throw new Error(error.detail || "Failed to delete documents");
  }
}

/** Clear the user's persistent memory. */
export async function clearMemory(): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/memory`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to clear memory" }));
    throw new Error(error.detail || "Failed to clear memory");
  }
}

/** Delete all user data (conversations, documents, tokens). */
export async function deleteAllUserData(): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/user-data`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to delete user data" }));
    throw new Error(error.detail || "Failed to delete user data");
  }
}

// ============================================================
// Group API functions (membership required, write requires write permission)
// ============================================================

/** Get directory tree for a group the user belongs to. */
export async function getGroupDirectories(groupId: string): Promise<DirectoryTreeResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/directories`,
  );
  if (!res.ok) {
    throw new Error("Failed to fetch group directory tree");
  }
  const data: unknown = await res.json();
  return DirectoryTreeResponseSchema.parse(data);
}

/** Get document content from a group the user belongs to. */
export async function getGroupDocumentContent(groupId: string, filename: string): Promise<string> {
  const res = await authFetch(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/documents/${encodeFilePath(filename)}`,
  );
  if (!res.ok) {
    throw new Error("Failed to fetch group document content");
  }
  return res.text();
}

/** Fetch a group workspace asset (e.g. image) as a blob URL for display. */
export async function fetchGroupDocumentAsset(groupId: string, filepath: string): Promise<string> {
  const res = await authFetch(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/documents/${encodeFilePath(filepath)}`,
  );

  if (!res.ok) {
    throw new Error("Failed to fetch group document asset");
  }

  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

/** Upload a document to a group (write access required). */
export async function uploadGroupDocument(
  groupId: string,
  filename: string,
  file: File,
  options?: UploadDocumentOptions,
): Promise<UploadDocumentResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const filepath = options?.targetDirectory ? `${options.targetDirectory}/${filename}` : filename;

  const url = `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/documents/${encodeFilePath(filepath)}`;

  if (options?.overwrite) {
    formData.append("overwrite", "true");
  }
  if (options?.spec) {
    formData.append("pipeline_spec", JSON.stringify(options.spec));
  }
  if (requiresConversion(filename) && options?.llm) {
    formData.append("llm_config", JSON.stringify(options.llm));
  }

  const res = await authFetch(url, { method: "PUT", body: formData });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(error.detail || "Upload failed");
  }
  const data: unknown = await res.json();
  return UploadDocumentResponseSchema.parse(data);
}

/** Upload a collection to a group (write access required). */
export async function uploadGroupCollection(
  groupId: string,
  file: File,
  options?: UploadCollectionOptions,
): Promise<CollectionUploadResponse> {
  const url = `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/documents/collections`;
  const res = await authFetch(url, {
    method: "POST",
    body: buildCollectionFormData(file, options),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Collection upload failed" }));
    throw new Error(error.detail || "Collection upload failed");
  }
  const data: unknown = await res.json();
  return CollectionUploadResponseSchema.parse(data);
}

/** Delete a document from a group (write access required). */
export async function deleteGroupDocument(groupId: string, filename: string): Promise<void> {
  const res = await authFetch(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/documents/${encodeFilePath(filename)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Delete failed" }));
    throw new Error(error.detail || "Delete failed");
  }
}

/** Create a directory in a group (write access required). */
export async function createGroupDirectory(
  groupId: string,
  path: string,
): Promise<CreateDirectoryResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/directories`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    },
  );
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to create directory" }));
    throw new Error(error.detail || "Failed to create directory");
  }
  const data: unknown = await res.json();
  return CreateDirectoryResponseSchema.parse(data);
}

/** Delete a directory from a group (write access required). */
export async function deleteGroupDirectory(
  groupId: string,
  dirpath: string,
): Promise<DeleteDirectoryResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/groups/${encodeURIComponent(groupId)}/directories`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: dirpath }),
    },
  );
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to delete directory" }));
    throw new Error(error.detail || "Failed to delete directory");
  }
  const data: unknown = await res.json();
  return DeleteDirectoryResponseSchema.parse(data);
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
    const error = await res.json().catch(() => ({ detail: "Admin action failed" }));
    throw new Error(error.detail || "Admin action failed");
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
  const res = await authFetch(`${API_BASE_URL}/api/admin/users`);
  if (!res.ok) throw new Error("Failed to list users");
  return AdminListUsersResponseSchema.parse(await res.json()).users;
}

/** List every group known to the local database. */
export async function adminListGroups(): Promise<AdminGroupInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/admin/groups`);
  if (!res.ok) throw new Error("Failed to list groups");
  return AdminListGroupsResponseSchema.parse(await res.json()).groups;
}

/** Wipe all data owned by a single user (workspace + SQL + index). */
export async function adminDeleteUserData(userId: string): Promise<void> {
  const res = await authFetch(
    `${API_BASE_URL}/api/admin/users/${encodeURIComponent(userId)}/data`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to delete user data" }));
    throw new Error(error.detail || "Failed to delete user data");
  }
}

/** Wipe all data owned by a single group (workspace + SQL + index). */
export async function adminDeleteGroupData(groupId: string): Promise<void> {
  const res = await authFetch(
    `${API_BASE_URL}/api/admin/groups/${encodeURIComponent(groupId)}/data`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Failed to delete group data" }));
    throw new Error(error.detail || "Failed to delete group data");
  }
}
