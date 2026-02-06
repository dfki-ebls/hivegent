import type { UIMessage } from '@ai-sdk/react';
import type {
  ChatRequestConfig,
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

/** Convert ChatRequestConfig to HTTP headers for chat requests. */
export function chatConfigToHeaders(config: ChatRequestConfig): Record<string, string> {
  return {
    'x-conversation-id': config.conversationId,
    'x-model': config.model,
    'x-api-key': config.apiKey,
    'x-base-url': config.baseUrl ?? '',
    'x-personality': config.personality,
  };
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
  pipeline?: ConversionPipeline;
  visionModel?: string;
  apiKey?: string;
  baseUrl?: string;
}

/** Response from document upload. */
export interface UploadDocumentResponse {
  filename: string;
  converted_filename: string | null;
  size_bytes: number;
  pipeline_used: string | null;
  message: string;
}

export async function uploadDocument(
  filename: string,
  file: File,
  options?: UploadDocumentOptions
): Promise<UploadDocumentResponse> {
  const formData = new FormData();
  formData.append('file', file);

  // Build URL with pipeline query parameter if conversion is needed
  let url = `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}`;
  if (requiresConversion(filename) && options?.pipeline) {
    url += `?pipeline=${encodeURIComponent(options.pipeline)}`;
  }

  // Build headers for conversion
  const headers: Record<string, string> = {};
  if (requiresConversion(filename)) {
    if (options?.visionModel) {
      headers['x-vision-model'] = options.visionModel;
    }
    if (options?.apiKey) {
      headers['x-api-key'] = options.apiKey;
    }
    if (options?.baseUrl) {
      headers['x-base-url'] = options.baseUrl;
    }
  }

  const res = await authFetch(url, {
    method: 'PUT',
    headers,
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
  model: string,
  apiKey: string,
  baseUrl: string
): Promise<GenerateTitleResponse> {
  const res = await authFetch(
    `${API_BASE_URL}/api/conversation/${conversationId}/generate-title`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        api_key: apiKey,
        base_url: baseUrl || null,
      }),
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
