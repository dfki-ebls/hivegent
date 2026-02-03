import type { UIMessage } from '@ai-sdk/react';
import type { CreateConversationResponse, DocumentInfo } from './types';

export const API_BASE_URL =
  import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

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
