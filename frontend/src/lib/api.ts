import type { UIMessage } from "@ai-sdk/react";
import { z } from "zod";

import {
  type BackendSettings,
  BackendSettingsSchema,
  type ChunkedDocumentResponse,
  ChunkedDocumentResponseSchema,
  type ChunkingPipeline,
  type ChunkingPipelineInfo,
  ChunkingPipelineInfoSchema,
  type CollectionUploadResponse,
  CollectionUploadResponseSchema,
  type CompactConversationResponse,
  CompactConversationResponseSchema,
  ConversationListResponseSchema,
  type ConversationSummary,
  ConversationSummarySchema,
  type ConversionPipeline,
  type ConversionPipelineInfo,
  ConversionPipelineInfoSchema,
  CreateConversationResponseSchema,
  type CreateDirectoryResponse,
  CreateDirectoryResponseSchema,
  type CreateTokenRequest,
  type CreateTokenResponse,
  CreateTokenResponseSchema,
  type DeleteDirectoryResponse,
  DeleteDirectoryResponseSchema,
  type DirectoryTreeResponse,
  DirectoryTreeResponseSchema,
  type DocumentInfo,
  DocumentListResponseSchema,
  type DocumentReference,
  DocumentReferenceSchema,
  type GenerateTitleResponse,
  GenerateTitleResponseSchema,
  type LlmConfig,
  type MoveDocumentResponse,
  MoveDocumentResponseSchema,
  type TokenInfo,
  TokenInfoSchema,
  type UploadDocumentResponse,
  UploadDocumentResponseSchema,
} from "./types";

export const API_BASE_URL =
  import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Token provider function set by AuthProvider
let getAccessToken: (() => Promise<string>) | null = null;

/**
 * Set the auth token provider function.
 * Called by AuthProvider when user is authenticated.
 */
export function setAuthTokenProvider(provider: () => Promise<string>) {
  getAccessToken = provider;
}

/**
 * Clear the auth token provider.
 * Called on logout.
 */
export function clearAuthTokenProvider() {
  getAccessToken = null;
}

/**
 * Make an authenticated fetch request.
 */
async function authFetch(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(options.headers);

  if (getAccessToken) {
    const token = await getAccessToken();
    headers.set("Authorization", `Bearer ${token}`);
  }

  return fetch(url, { ...options, headers });
}

/**
 * Get the current auth headers for use with external transports.
 */
export async function getAuthHeaders(): Promise<Record<string, string>> {
  if (getAccessToken) {
    const token = await getAccessToken();
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
  const ext = "." + (filename.split(".").pop()?.toLowerCase() ?? "");
  return ext !== ".md";
}

/** Fetch server-side LLM settings. */
export async function fetchSettings(): Promise<BackendSettings> {
  const res = await authFetch(`${API_BASE_URL}/api/settings`);
  if (!res.ok) {
    throw new Error("Failed to fetch settings");
  }
  const data: unknown = await res.json();
  return BackendSettingsSchema.parse(data);
}

/** Build a sparse LlmConfig from frontend settings. */
export function buildLlmConfig(s: {
  model?: string;
  apiKey?: string;
  baseUrl?: string;
}): LlmConfig {
  const config: LlmConfig = {};
  if (s.model) config.model = s.model;
  if (s.apiKey) config.api_key = s.apiKey;
  if (s.baseUrl) config.base_url = s.baseUrl;
  return config;
}

export async function createConversation(): Promise<string> {
  const res = await authFetch(`${API_BASE_URL}/api/conversation`, {
    method: "POST",
  });
  const data: unknown = await res.json();
  return CreateConversationResponseSchema.parse(data).id;
}

export async function getMessages(
  conversationId: string,
): Promise<UIMessage[]> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}/messages`,
  );
  if (!res.ok) {
    return [];
  }
  return res.json();
}

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
  conversionPipeline?: ConversionPipeline;
  chunkingPipeline?: ChunkingPipeline;
  llm?: LlmConfig;
  targetDirectory?: string;
}

export async function uploadDocument(
  filename: string,
  file: File,
  options?: UploadDocumentOptions,
): Promise<UploadDocumentResponse> {
  const formData = new FormData();
  formData.append("file", file);

  // Build the filepath, optionally prepending the target directory
  const filepath = options?.targetDirectory
    ? `${options.targetDirectory}/${filename}`
    : filename;

  // Build URL with query parameters
  let url = `${API_BASE_URL}/api/documents/${encodeFilePath(filepath)}`;
  const params = new URLSearchParams();
  if (requiresConversion(filename) && options?.conversionPipeline) {
    params.set("conversion_pipeline", options.conversionPipeline);
  }
  if (options?.chunkingPipeline) {
    params.set("chunking_pipeline", options.chunkingPipeline);
  }
  const queryString = params.toString();
  if (queryString) {
    url += `?${queryString}`;
  }

  // Add LLM config as form field for binary files requiring conversion
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
  conversionPipeline?: ConversionPipeline;
  chunkingPipeline?: ChunkingPipeline;
  llm?: LlmConfig;
}

/** Upload a markdown collection as a ZIP archive. */
export async function uploadCollection(
  file: File,
  options?: UploadCollectionOptions,
): Promise<CollectionUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  let url = `${API_BASE_URL}/api/collections`;
  const params = new URLSearchParams();
  if (options?.conversionPipeline) {
    params.set("conversion_pipeline", options.conversionPipeline);
  }
  if (options?.chunkingPipeline) {
    params.set("chunking_pipeline", options.chunkingPipeline);
  }
  const queryString = params.toString();
  if (queryString) {
    url += `?${queryString}`;
  }

  if (options?.llm) {
    formData.append("llm_config", JSON.stringify(options.llm));
  }

  const res = await authFetch(url, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: "Collection upload failed" }));
    throw new Error(error.detail || "Collection upload failed");
  }

  const data: unknown = await res.json();
  return CollectionUploadResponseSchema.parse(data);
}

export async function deleteDocument(filename: string): Promise<void> {
  const res = await authFetch(
    `${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`,
    {
      method: "DELETE",
    },
  );

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Delete failed" }));
    throw new Error(error.detail || "Delete failed");
  }
}

export async function getDocumentContent(filename: string): Promise<string> {
  const res = await authFetch(
    `${API_BASE_URL}/api/documents/${encodeFilePath(filename)}`,
  );

  if (!res.ok) {
    throw new Error("Failed to fetch document content");
  }

  return res.text();
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations`);
  if (!res.ok) {
    throw new Error("Failed to list conversations");
  }
  const data: unknown = await res.json();
  return ConversationListResponseSchema.parse(data).conversations;
}

export async function getConversationDocumentReferences(
  conversationId: string,
): Promise<DocumentReference[]> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}/document-references`,
  );
  if (!res.ok) {
    return [];
  }
  const data: unknown = await res.json();
  return z.array(DocumentReferenceSchema).parse(data);
}

export async function updateConversationTitle(
  conversationId: string,
  title: string,
): Promise<ConversationSummary> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}/title`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    },
  );

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
    `${API_BASE_URL}/api/conversation/${conversationId}/generate-title`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm }),
    },
  );

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: "Title generation failed" }));
    throw new Error(error.detail || "Title generation failed");
  }

  const data: unknown = await res.json();
  return GenerateTitleResponseSchema.parse(data);
}

export async function compactConversation(
  conversationId: string,
): Promise<CompactConversationResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}/compact`,
    { method: "POST" },
  );

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: "Compaction failed" }));
    throw new Error(error.detail || "Compaction failed");
  }

  const data: unknown = await res.json();
  return CompactConversationResponseSchema.parse(data);
}

export async function getConversation(
  conversationId: string,
): Promise<ConversationSummary | null> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}`,
  );
  if (!res.ok) return null;
  const data: unknown = await res.json();
  return ConversationSummarySchema.parse(data);
}

export async function deleteConversation(
  conversationId: string,
): Promise<void> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}`,
    {
      method: "DELETE",
    },
  );

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Delete failed" }));
    throw new Error(error.detail || "Delete failed");
  }
}

// Token management API functions

export async function createToken(
  name: string,
  expiresInDays?: number,
): Promise<CreateTokenResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/tokens`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      expires_in_days: expiresInDays ?? null,
    } satisfies CreateTokenRequest),
  });

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: "Failed to create token" }));
    throw new Error(error.detail || "Failed to create token");
  }

  const data: unknown = await res.json();
  return CreateTokenResponseSchema.parse(data);
}

export async function listTokens(): Promise<TokenInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/tokens`);

  if (!res.ok) {
    throw new Error("Failed to list tokens");
  }

  const data: unknown = await res.json();
  return z.array(TokenInfoSchema).parse(data);
}

export async function revokeToken(tokenId: string): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/tokens/${tokenId}`, {
    method: "DELETE",
  });

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: "Failed to revoke token" }));
    throw new Error(error.detail || "Failed to revoke token");
  }
}

// Conversion pipeline API functions

export async function getConversionPipelines(): Promise<
  ConversionPipelineInfo[]
> {
  const res = await authFetch(`${API_BASE_URL}/api/conversion-pipelines`);

  if (!res.ok) {
    throw new Error("Failed to get conversion pipelines");
  }

  const data: unknown = await res.json();
  return z.array(ConversionPipelineInfoSchema).parse(data);
}

// Chunking pipeline API functions

export async function getChunkingPipelines(): Promise<ChunkingPipelineInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/chunking-pipelines`);

  if (!res.ok) {
    throw new Error("Failed to get chunking pipelines");
  }

  const data: unknown = await res.json();
  return z.array(ChunkingPipelineInfoSchema).parse(data);
}

export async function getDocumentChunks(
  filename: string,
): Promise<ChunkedDocumentResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}/chunks`,
  );

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: "Failed to fetch chunks" }));
    throw new Error(error.detail || "Failed to fetch chunks");
  }

  const data: unknown = await res.json();
  return ChunkedDocumentResponseSchema.parse(data);
}

/** Options for document reconversion. */
export interface ReconvertDocumentOptions {
  conversionPipeline?: ConversionPipeline;
  chunkingPipeline?: ChunkingPipeline;
  llm?: LlmConfig;
}

export async function reconvertDocument(
  filename: string,
  options?: ReconvertDocumentOptions,
): Promise<UploadDocumentResponse> {
  const url = `${API_BASE_URL}/api/documents/${encodeFilePath(filename)}/reconvert`;

  const res = await authFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversion_pipeline: options?.conversionPipeline ?? "auto",
      chunking_pipeline: options?.chunkingPipeline ?? "auto",
      llm: options?.llm ?? {},
    }),
  });

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: "Reconvert failed" }));
    throw new Error(error.detail || "Reconvert failed");
  }

  const data: unknown = await res.json();
  return UploadDocumentResponseSchema.parse(data);
}

export async function rechunkDocument(
  filename: string,
  chunkingPipeline?: ChunkingPipeline,
  chunkSize?: number,
): Promise<ChunkedDocumentResponse> {
  const params = new URLSearchParams();
  if (chunkingPipeline) {
    params.set("chunking_pipeline", chunkingPipeline);
  }
  if (chunkSize) {
    params.set("chunk_size", chunkSize.toString());
  }

  let url = `${API_BASE_URL}/api/documents/${encodeFilePath(filename)}/rechunk`;
  const queryString = params.toString();
  if (queryString) {
    url += `?${queryString}`;
  }

  const res = await authFetch(url, { method: "POST" });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Rechunk failed" }));
    throw new Error(error.detail || "Rechunk failed");
  }

  const data: unknown = await res.json();
  return ChunkedDocumentResponseSchema.parse(data);
}

// Directory management API functions

export async function getDirectoryTree(): Promise<DirectoryTreeResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/directories/tree`);
  if (!res.ok) {
    throw new Error("Failed to fetch directory tree");
  }
  const data: unknown = await res.json();
  return DirectoryTreeResponseSchema.parse(data);
}

export async function createDirectory(
  path: string,
): Promise<CreateDirectoryResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/directories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: "Failed to create directory" }));
    throw new Error(error.detail || "Failed to create directory");
  }

  const data: unknown = await res.json();
  return CreateDirectoryResponseSchema.parse(data);
}

export async function deleteDirectory(
  dirpath: string,
): Promise<DeleteDirectoryResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/directories/${encodeFilePath(dirpath)}`,
    { method: "DELETE" },
  );

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: "Failed to delete directory" }));
    throw new Error(error.detail || "Failed to delete directory");
  }

  const data: unknown = await res.json();
  return DeleteDirectoryResponseSchema.parse(data);
}

export async function moveDocument(
  filepath: string,
  destination: string,
): Promise<MoveDocumentResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/documents/${encodeFilePath(filepath)}/move`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination }),
    },
  );

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Move failed" }));
    throw new Error(error.detail || "Move failed");
  }

  const data: unknown = await res.json();
  return MoveDocumentResponseSchema.parse(data);
}
