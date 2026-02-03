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
  content: string;
}

export interface GrepMatch {
  filename: string;
  line_number: number;
  line: string;
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
