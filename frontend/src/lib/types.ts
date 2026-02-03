export interface CreateConversationResponse {
  id: string;
}

export enum FileExtension {
  TXT = '.txt',
  MD = '.md',
}

export interface DocumentInfo {
  filename: string;
  size_bytes: number;
  modified_at: string;
}

export interface DocumentStats {
  line_count: number;
  word_count: number;
  char_count: number;
}

export interface DocumentRange {
  start_line: number;
  end_line: number;
  total_lines: number;
  content: string;
}

export interface GrepMatch {
  filename: string;
  line: number;
  content: string | null;
}

export interface SearchDocumentsInput {
  query: string;
  top_k?: number;
}

export interface RetrievedDocument {
  filename: string;
  content: string;
  score: number;
}

/** Frontend-only type for storing fetched document content. */
export interface StoredDocument {
  filename: string;
  content: string;
  score?: number;
  sources: string[];
}

/** A reference to a document accessed during a conversation. */
export interface DocumentReference {
  filename: string;
  sources: string[];
  score?: number;
}

/** Summary information for listing conversations. */
export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

/** Response for listing conversations. */
export interface ConversationListResponse {
  conversations: ConversationSummary[];
  total_count: number;
}

/** Response for title generation. */
export interface GenerateTitleResponse {
  title: string;
}

/** Available assistant personalities. */
export type Personality = 'default' | 'concise' | 'detailed';

/** Personality option for display in UI. */
export interface PersonalityOption {
  value: Personality;
  label: string;
  description: string;
}

/** Available personality options. */
export const PERSONALITY_OPTIONS: PersonalityOption[] = [
  {
    value: 'default',
    label: 'Default',
    description: 'Helpful and accurate with source citations',
  },
  {
    value: 'concise',
    label: 'Concise',
    description: 'Brief, to-the-point responses',
  },
  {
    value: 'detailed',
    label: 'Detailed',
    description: 'Thorough explanations with comprehensive context',
  },
];

/** Configuration for chat requests, passed via HTTP headers. */
export interface ChatRequestConfig {
  conversationId: string;
  model: string;
  apiKey: string;
  baseUrl?: string;
  personality: Personality;
}
