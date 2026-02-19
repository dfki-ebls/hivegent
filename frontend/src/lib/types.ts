/**
 * Shared types and Zod schemas.
 *
 * All data that crosses trust boundaries (API responses, localStorage) has a
 * Zod schema for runtime validation.  TypeScript types are derived from schemas
 * with `z.infer` wherever possible.  Plain interfaces are kept only for
 * frontend-only structures and outgoing request payloads.
 */

import { z } from 'zod';

// ============================================================
// Enums
// ============================================================

/** Available conversion pipelines for document conversion. */
export enum ConversionPipeline {
  AUTO = 'auto',
  LLM = 'llm',
  MARKER = 'marker',
  DOCLING = 'docling',
  MINERU = 'mineru',
  PANDOC = 'pandoc',
}

/** Available chunking pipelines. */
export enum ChunkingPipeline {
  AUTO = 'auto',
  TOKEN = 'token',
  SENTENCE = 'sentence',
  RECURSIVE = 'recursive',
}

// Zod v4: z.enum() accepts TS enums directly (replaces deprecated nativeEnum)
export const ConversionPipelineSchema = z.enum(ConversionPipeline);
export const ChunkingPipelineSchema = z.enum(ChunkingPipeline);

// ============================================================
// Persisted data schemas (localStorage)
// ============================================================

export const DocumentTabSchema = z.enum(['fetched', 'manage']);
export type DocumentTab = z.infer<typeof DocumentTabSchema>;

export const ExpandedDirsSchema = z.array(z.string());

/** User-provided overrides stored in localStorage. Empty string = use backend default. */
export const UserOverridesSchema = z.object({
  model: z.string(),
  apiKey: z.string(),
  baseUrl: z.string(),
  smallModel: z.string(),
  visionModel: z.string(),
});
export type UserOverrides = z.infer<typeof UserOverridesSchema>;

// ============================================================
// API response schemas
// ============================================================

/** Settings exposed by the backend. */
export const BackendSettingsSchema = z.object({
  model: z.string(),
  vision_model: z.string(),
  small_model: z.string(),
  has_api_key: z.boolean(),
  base_url: z.string(),
});
export type BackendSettings = z.infer<typeof BackendSettingsSchema>;

export const CreateConversationResponseSchema = z.object({
  id: z.string(),
});
export type CreateConversationResponse = z.infer<typeof CreateConversationResponseSchema>;

/** Metadata for a conversion pipeline, fetched from the backend. */
export const ConversionPipelineInfoSchema = z.object({
  value: z.string(),
  label: z.string(),
  description: z.string(),
  extensions: z.array(z.string()),
});
export type ConversionPipelineInfo = z.infer<typeof ConversionPipelineInfoSchema>;

/** Metadata for a chunking pipeline, fetched from the backend. */
export const ChunkingPipelineInfoSchema = z.object({
  value: z.string(),
  label: z.string(),
  description: z.string(),
});
export type ChunkingPipelineInfo = z.infer<typeof ChunkingPipelineInfoSchema>;

/** A single chunk within a chunked document. */
export const ChunkInfoSchema = z.object({
  text: z.string(),
  token_count: z.number(),
  start_index: z.number(),
  end_index: z.number(),
  index: z.number(),
});
export type ChunkInfo = z.infer<typeof ChunkInfoSchema>;

/** Response from the chunks endpoint. */
export const ChunkedDocumentResponseSchema = z.object({
  chunking_pipeline: z.string(),
  chunk_size: z.number(),
  created_at: z.string(),
  chunk_count: z.number(),
  chunks: z.array(ChunkInfoSchema),
});
export type ChunkedDocumentResponse = z.infer<typeof ChunkedDocumentResponseSchema>;

export const DocumentInfoSchema = z.object({
  filename: z.string(),
  size_bytes: z.number(),
  modified_at: z.string(),
  chunk_count: z.number().nullable().optional(),
  has_original: z.boolean(),
});
export type DocumentInfo = z.infer<typeof DocumentInfoSchema>;

export const DocumentListResponseSchema = z.object({
  documents: z.array(DocumentInfoSchema),
  total_count: z.number(),
});
export type DocumentListResponse = z.infer<typeof DocumentListResponseSchema>;

export const DocumentStatsSchema = z.object({
  line_count: z.number(),
  word_count: z.number(),
  char_count: z.number(),
});
export type DocumentStats = z.infer<typeof DocumentStatsSchema>;

export const DocumentRangeSchema = z.object({
  start_line: z.number(),
  end_line: z.number(),
  total_lines: z.number(),
  content: z.string(),
});
export type DocumentRange = z.infer<typeof DocumentRangeSchema>;

export const GrepMatchSchema = z.object({
  filename: z.string(),
  line: z.number(),
  content: z.string().nullable(),
});
export type GrepMatch = z.infer<typeof GrepMatchSchema>;

export const RetrievedDocumentSchema = z.object({
  filename: z.string(),
  content: z.string(),
  score: z.number(),
});
export type RetrievedDocument = z.infer<typeof RetrievedDocumentSchema>;

export const RetrievedChunkSchema = z.object({
  filename: z.string(),
  chunk_index: z.number(),
  text: z.string(),
  token_count: z.number(),
  score: z.number(),
});
export type RetrievedChunk = z.infer<typeof RetrievedChunkSchema>;

/** A reference to a document accessed during a conversation. */
export const DocumentReferenceSchema = z.object({
  filename: z.string(),
  sources: z.array(z.string()),
  score: z.number().optional(),
});
export type DocumentReference = z.infer<typeof DocumentReferenceSchema>;

/** Summary information for listing conversations. */
export const ConversationSummarySchema = z.object({
  id: z.string(),
  title: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  message_count: z.number(),
  compacted_from: z.string().nullable().optional(),
});
export type ConversationSummary = z.infer<typeof ConversationSummarySchema>;

/** Response for listing conversations. */
export const ConversationListResponseSchema = z.object({
  conversations: z.array(ConversationSummarySchema),
  total_count: z.number(),
});
export type ConversationListResponse = z.infer<typeof ConversationListResponseSchema>;

/** Response for conversation compaction. */
export const CompactConversationResponseSchema = z.object({
  new_conversation_id: z.string(),
  summary: z.string(),
  message: z.string(),
});
export type CompactConversationResponse = z.infer<typeof CompactConversationResponseSchema>;

/** Response for title generation. */
export const GenerateTitleResponseSchema = z.object({
  title: z.string(),
});
export type GenerateTitleResponse = z.infer<typeof GenerateTitleResponseSchema>;

/** Response from token creation. */
export const CreateTokenResponseSchema = z.object({
  token: z.string(),
  id: z.string(),
  name: z.string(),
  created_at: z.string(),
  expires_at: z.string().nullable(),
});
export type CreateTokenResponse = z.infer<typeof CreateTokenResponseSchema>;

/** Information about a personal access token (without the token value). */
export const TokenInfoSchema = z.object({
  id: z.string(),
  name: z.string(),
  created_at: z.string(),
  expires_at: z.string().nullable(),
  last_used_at: z.string().nullable(),
});
export type TokenInfo = z.infer<typeof TokenInfoSchema>;

/** A file or directory entry in the document tree (recursive). */
export interface DirectoryEntry {
  type: 'file' | 'directory';
  name: string;
  path: string;
  size_bytes?: number | null;
  modified_at?: string | null;
  chunk_count?: number | null;
  has_original?: boolean;
  children?: DirectoryEntry[] | null;
}

export const DirectoryEntrySchema: z.ZodType<DirectoryEntry> = z.object({
  type: z.enum(['file', 'directory']),
  name: z.string(),
  path: z.string(),
  size_bytes: z.number().nullable().optional(),
  modified_at: z.string().nullable().optional(),
  chunk_count: z.number().nullable().optional(),
  has_original: z.boolean().optional(),
  children: z.lazy(() => z.array(DirectoryEntrySchema)).nullable().optional(),
});

/** Response from the directory tree endpoint. */
export const DirectoryTreeResponseSchema = z.object({
  root: DirectoryEntrySchema,
  total_files: z.number(),
  total_directories: z.number(),
});
export type DirectoryTreeResponse = z.infer<typeof DirectoryTreeResponseSchema>;

/** Response from creating a directory. */
export const CreateDirectoryResponseSchema = z.object({
  path: z.string(),
  message: z.string(),
});
export type CreateDirectoryResponse = z.infer<typeof CreateDirectoryResponseSchema>;

/** Response from moving a document. */
export const MoveDocumentResponseSchema = z.object({
  source: z.string(),
  destination: z.string(),
  message: z.string(),
});
export type MoveDocumentResponse = z.infer<typeof MoveDocumentResponseSchema>;

/** Response from deleting a directory. */
export const DeleteDirectoryResponseSchema = z.object({
  path: z.string(),
  files_deleted: z.number(),
  message: z.string(),
});
export type DeleteDirectoryResponse = z.infer<typeof DeleteDirectoryResponseSchema>;

/** Response from document upload. */
export const UploadDocumentResponseSchema = z.object({
  filename: z.string(),
  converted_filename: z.string().nullable(),
  size_bytes: z.number(),
  conversion_pipeline_used: z.string().nullable(),
  chunk_count: z.number().nullable(),
  chunking_pipeline_used: z.string().nullable(),
  message: z.string(),
});
export type UploadDocumentResponse = z.infer<typeof UploadDocumentResponseSchema>;

/** Response from collection (directory/ZIP) upload. */
export const CollectionUploadResponseSchema = z.object({
  total_files: z.number(),
  markdown_files: z.number(),
  converted_attachments: z.number(),
  failed_files: z.array(z.string()),
  message: z.string(),
});
export type CollectionUploadResponse = z.infer<typeof CollectionUploadResponseSchema>;

// ============================================================
// Frontend-only types (no runtime validation needed)
// ============================================================

/** Frontend-only type for storing fetched document content. */
export interface StoredDocument {
  filename: string;
  content: string;
  score?: number;
  sources: string[];
}

// ============================================================
// Request types (sent to backend, no validation needed)
// ============================================================

export interface SearchDocumentsInput {
  query: string;
  top_k?: number;
}

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

// ============================================================
// Display constants
// ============================================================

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
