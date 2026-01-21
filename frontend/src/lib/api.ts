import type { UIMessage } from '@ai-sdk/react';
import type { CreateConversationResponse } from './types';

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
