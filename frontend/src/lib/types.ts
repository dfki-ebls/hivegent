export interface CreateConversationResponse {
  id: string;
}

export enum FileExtension {
  // Text formats (stored as-is)
  TXT = '.txt',
  MD = '.md',
  HTML = '.html',
  XML = '.xml',
  CSV = '.csv',
  ADOC = '.adoc',

  // Binary formats (require conversion)
  DOCX = '.docx',
  XLSX = '.xlsx',
  PPTX = '.pptx',
  PDF = '.pdf',
  PNG = '.png',
  JPG = '.jpg',
  JPEG = '.jpeg',
}

/** Available conversion pipelines for binary documents. */
export enum ConversionPipeline {
  AUTO = 'auto',
  LLM = 'llm',
  MARKER = 'marker',
  DOCLING = 'docling',
  MINERU = 'mineru',
}

/** Available chunking pipelines. */
export enum ChunkingPipeline {
  AUTO = 'auto',
  TOKEN = 'token',
  SENTENCE = 'sentence',
  RECURSIVE = 'recursive',
}

/** Metadata for a conversion pipeline, fetched from the backend. */
export interface ConversionPipelineInfo {
  value: string;
  label: string;
  description: string;
  extensions: string[];
}

/** Metadata for a chunking pipeline, fetched from the backend. */
export interface ChunkingPipelineInfo {
  value: string;
  label: string;
  description: string;
}

/** A single chunk within a chunked document. */
export interface ChunkInfo {
  text: string;
  token_count: number;
  start_index: number;
  end_index: number;
  index: number;
}

/** Response from the chunks endpoint. */
export interface ChunkedDocumentResponse {
  chunking_pipeline: string;
  chunk_size: number;
  created_at: string;
  chunk_count: number;
  chunks: ChunkInfo[];
}

/** Text-based extensions that don't require conversion. */
export const TEXT_EXTENSIONS = new Set([
  FileExtension.TXT,
  FileExtension.MD,
  FileExtension.HTML,
  FileExtension.XML,
  FileExtension.CSV,
  FileExtension.ADOC,
]);

/** Binary extensions that require conversion. */
export const BINARY_EXTENSIONS = new Set([
  FileExtension.DOCX,
  FileExtension.XLSX,
  FileExtension.PPTX,
  FileExtension.PDF,
  FileExtension.PNG,
  FileExtension.JPG,
  FileExtension.JPEG,
]);

/** Check if a file extension requires conversion. */
export function requiresConversion(filename: string): boolean {
  const ext = ('.' + filename.split('.').pop()?.toLowerCase()) as FileExtension;
  return BINARY_EXTENSIONS.has(ext);
}

export interface DocumentInfo {
  filename: string;
  size_bytes: number;
  modified_at: string;
  chunk_count?: number | null;
  has_original: boolean;
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

/** LLM provider configuration sent to the backend. */
export interface LlmConfig {
  model?: string;
  api_key?: string;
  base_url?: string | null;
}

/** Request to create a personal access token. */
export interface CreateTokenRequest {
  name: string;
  expires_in_days: number | null;
}

/** Response from token creation. */
export interface CreateTokenResponse {
  token: string;
  id: string;
  name: string;
  created_at: string;
  expires_at: string | null;
}

/** Information about a personal access token (without the token value). */
export interface TokenInfo {
  id: string;
  name: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}
