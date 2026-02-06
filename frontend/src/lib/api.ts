import type { UIMessage } from '@ai-sdk/react';
import type {
  ChunkedDocumentResponse,
  ChunkingPipeline,
  ChunkingPipelineInfo,
  ConversionPipeline,
  ConversionPipelineInfo,
  ConversationListResponse,
  ConversationSummary,
  CreateTokenRequest,
  CreateTokenResponse,
  CreateConversationResponse,
  DocumentInfo,
  DocumentReference,
  GenerateTitleResponse,
  LlmConfig,
  TokenInfo,
} from './types';
import { requiresConversion } from './types';

export const API_BASE_URL =
  import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

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
async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);

  if (getAccessToken) {
    const token = await getAccessToken();
    headers.set('Authorization', `Bearer ${token}`);
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

/** Settings exposed by the backend. */
export interface BackendSettings {
  model: string;
  vision_model: string;
  small_model: string;
  has_api_key: boolean;
  base_url: string;
}

/** Fetch server-side LLM settings. */
export async function fetchSettings(): Promise<BackendSettings> {
  const res = await authFetch(`${API_BASE_URL}/api/settings`);
  if (!res.ok) {
    throw new Error('Failed to fetch settings');
  }
  return res.json();
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
    method: 'POST',
  });
  const data: CreateConversationResponse = await res.json();
  return data.id;
}

export async function getMessages(conversationId: string): Promise<UIMessage[]> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}/messages`
  );
  if (!res.ok) {
    return [];
  }
  return res.json();
}

export interface DocumentListResponse {
  documents: DocumentInfo[];
  total_count: number;
}

export async function listDocuments(): Promise<DocumentInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/documents`);
  if (!res.ok) {
    throw new Error('Failed to list documents');
  }
  const data: DocumentListResponse = await res.json();
  return data.documents;
}

/** Options for document upload. */
export interface UploadDocumentOptions {
  conversionPipeline?: ConversionPipeline;
  chunkingPipeline?: ChunkingPipeline;
  llm?: LlmConfig;
}

/** Response from document upload. */
export interface UploadDocumentResponse {
  filename: string;
  converted_filename: string | null;
  size_bytes: number;
  conversion_pipeline_used: string | null;
  chunk_count: number | null;
  chunking_pipeline_used: string | null;
  message: string;
}

export async function uploadDocument(
  filename: string,
  file: File,
  options?: UploadDocumentOptions
): Promise<UploadDocumentResponse> {
  const formData = new FormData();
  formData.append('file', file);

  // Build URL with query parameters
  let url = `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}`;
  const params = new URLSearchParams();
  if (requiresConversion(filename) && options?.conversionPipeline) {
    params.set('conversion_pipeline', options.conversionPipeline);
  }
  if (options?.chunkingPipeline) {
    params.set('chunking_pipeline', options.chunkingPipeline);
  }
  const queryString = params.toString();
  if (queryString) {
    url += `?${queryString}`;
  }

  // Add LLM config as form field for binary files requiring conversion
  if (requiresConversion(filename) && options?.llm) {
    formData.append('llm_config', JSON.stringify(options.llm));
  }

  const res = await authFetch(url, {
    method: 'PUT',
    body: formData,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(error.detail || 'Upload failed');
  }

  return res.json();
}

export async function deleteDocument(filename: string): Promise<void> {
  const res = await authFetch(
    `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}`,
    {
      method: 'DELETE',
    }
  );

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Delete failed' }));
    throw new Error(error.detail || 'Delete failed');
  }
}

export async function getDocumentContent(filename: string): Promise<string> {
  const res = await authFetch(
    `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}`
  );

  if (!res.ok) {
    throw new Error('Failed to fetch document content');
  }

  return res.text();
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await authFetch(`${API_BASE_URL}/api/conversations`);
  if (!res.ok) {
    throw new Error('Failed to list conversations');
  }
  const data: ConversationListResponse = await res.json();
  return data.conversations;
}

export async function getConversationDocumentReferences(
  conversationId: string
): Promise<DocumentReference[]> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}/document-references`
  );
  if (!res.ok) {
    return [];
  }
  return res.json();
}

export async function updateConversationTitle(
  conversationId: string,
  title: string
): Promise<ConversationSummary> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}/title`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }
  );

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Update failed' }));
    throw new Error(error.detail || 'Update failed');
  }

  return res.json();
}

export async function generateConversationTitle(
  conversationId: string,
  llm: LlmConfig
): Promise<GenerateTitleResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}/generate-title`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ llm }),
    }
  );

  if (!res.ok) {
    const error = await res
      .json()
      .catch(() => ({ detail: 'Title generation failed' }));
    throw new Error(error.detail || 'Title generation failed');
  }

  return res.json();
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/conversation/${conversationId}`, {
    method: 'DELETE',
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Delete failed' }));
    throw new Error(error.detail || 'Delete failed');
  }
}

// Token management API functions

export async function createToken(
  name: string,
  expiresInDays?: number
): Promise<CreateTokenResponse> {
  const res = await authFetch(`${API_BASE_URL}/api/tokens`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name,
      expires_in_days: expiresInDays ?? null,
    } satisfies CreateTokenRequest),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to create token' }));
    throw new Error(error.detail || 'Failed to create token');
  }

  return res.json();
}

export async function listTokens(): Promise<TokenInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/tokens`);

  if (!res.ok) {
    throw new Error('Failed to list tokens');
  }

  return res.json();
}

export async function revokeToken(tokenId: string): Promise<void> {
  const res = await authFetch(`${API_BASE_URL}/api/tokens/${tokenId}`, {
    method: 'DELETE',
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to revoke token' }));
    throw new Error(error.detail || 'Failed to revoke token');
  }
}

// Conversion pipeline API functions

export async function getConversionPipelines(): Promise<ConversionPipelineInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/conversion-pipelines`);

  if (!res.ok) {
    throw new Error('Failed to get conversion pipelines');
  }

  return res.json();
}

// Chunking pipeline API functions

export async function getChunkingPipelines(): Promise<ChunkingPipelineInfo[]> {
  const res = await authFetch(`${API_BASE_URL}/api/chunking-pipelines`);

  if (!res.ok) {
    throw new Error('Failed to get chunking pipelines');
  }

  return res.json();
}

export async function getDocumentChunks(filename: string): Promise<ChunkedDocumentResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}/chunks`
  );

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Failed to fetch chunks' }));
    throw new Error(error.detail || 'Failed to fetch chunks');
  }

  return res.json();
}

/** Options for document reconversion. */
export interface ReconvertDocumentOptions {
  conversionPipeline?: ConversionPipeline;
  chunkingPipeline?: ChunkingPipeline;
  llm?: LlmConfig;
}

export async function reconvertDocument(
  filename: string,
  options?: ReconvertDocumentOptions
): Promise<UploadDocumentResponse> {
  const url = `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}/reconvert`;

  const res = await authFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      conversion_pipeline: options?.conversionPipeline ?? 'auto',
      chunking_pipeline: options?.chunkingPipeline ?? 'auto',
      llm: options?.llm ?? {},
    }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Reconvert failed' }));
    throw new Error(error.detail || 'Reconvert failed');
  }

  return res.json();
}

export async function rechunkDocument(
  filename: string,
  chunkingPipeline?: ChunkingPipeline,
  chunkSize?: number
): Promise<ChunkedDocumentResponse> {
  const params = new URLSearchParams();
  if (chunkingPipeline) {
    params.set('chunking_pipeline', chunkingPipeline);
  }
  if (chunkSize) {
    params.set('chunk_size', chunkSize.toString());
  }

  let url = `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}/rechunk`;
  const queryString = params.toString();
  if (queryString) {
    url += `?${queryString}`;
  }

  const res = await authFetch(url, { method: 'POST' });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Rechunk failed' }));
    throw new Error(error.detail || 'Rechunk failed');
  }

  return res.json();
}
