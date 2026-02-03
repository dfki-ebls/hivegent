import type { UIMessage } from '@ai-sdk/react';
import type {
  ChatRequestConfig,
  ConversationListResponse,
  ConversationSummary,
  CreateConversationResponse,
  DocumentInfo,
  DocumentReference,
  GenerateTitleResponse,
} from './types';

export const API_BASE_URL =
  import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

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
  const res = await fetch(`${API_BASE_URL}/api/conversation`, {
    method: 'POST',
  });
  const data: CreateConversationResponse = await res.json();
  return data.id;
}

export async function getMessages(conversationId: string): Promise<UIMessage[]> {
  const res = await fetch(
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
  const res = await fetch(`${API_BASE_URL}/api/documents`);
  if (!res.ok) {
    throw new Error('Failed to list documents');
  }
  const data: DocumentListResponse = await res.json();
  return data.documents;
}

export async function uploadDocument(
  filename: string,
  file: File
): Promise<void> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(
    `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}`,
    {
      method: 'PUT',
      body: formData,
    }
  );

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(error.detail || 'Upload failed');
  }
}

export async function deleteDocument(filename: string): Promise<void> {
  const res = await fetch(
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
  const res = await fetch(
    `${API_BASE_URL}/api/documents/${encodeURIComponent(filename)}`
  );

  if (!res.ok) {
    throw new Error('Failed to fetch document content');
  }

  return res.text();
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await fetch(`${API_BASE_URL}/api/conversations`);
  if (!res.ok) {
    throw new Error('Failed to list conversations');
  }
  const data: ConversationListResponse = await res.json();
  return data.conversations;
}

export async function getConversationDocumentReferences(
  conversationId: string
): Promise<DocumentReference[]> {
  const res = await fetch(
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
  const res = await fetch(
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
  const res = await fetch(
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
  const res = await fetch(`${API_BASE_URL}/api/conversation/${conversationId}`, {
    method: 'DELETE',
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: 'Delete failed' }));
    throw new Error(error.detail || 'Delete failed');
  }
}
