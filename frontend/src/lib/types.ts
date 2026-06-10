/**
 * Shared types and Zod schemas.
 *
 * All data that crosses trust boundaries (API responses, localStorage) has a
 * Zod schema for runtime validation.  TypeScript types are derived from schemas
 * with `z.infer` wherever possible.  Plain interfaces are kept only for
 * frontend-only structures and outgoing request payloads.
 */

import { z } from "zod";

// ============================================================
// Enums
// ============================================================

/** Available conversion pipelines for document conversion. */
export enum ConversionPipeline {
  AUTO = "auto",
  LLM = "llm",
  MARKER = "marker",
  DOCLING = "docling",
  MINERU = "mineru",
  PANDOC = "pandoc",
  MARKITDOWN = "markitdown",
  KREUZBERG = "kreuzberg",
  PDF_OXIDE = "pdf-oxide",
  TABLE_CHEF = "table-chef",
  TEXT_CHEF = "text-chef",
}

/** Available chunking pipelines. */
export enum ChunkingPipeline {
  AUTO = "auto",
  NONE = "none",
  TOKEN = "token",
  FAST = "fast",
  SENTENCE = "sentence",
  RECURSIVE = "recursive",
  TABLE = "table",
  MARKDOWN = "markdown",
  SEMANTIC = "semantic",
  CODE = "code",
  NEURAL = "neural",
  LATE = "late",
  SLUMBER = "slumber",
}

// Zod v4: z.enum() accepts TS enums directly (replaces deprecated nativeEnum)
export const ConversionPipelineSchema = z.enum(ConversionPipeline);
export const ChunkingPipelineSchema = z.enum(ChunkingPipeline);

/** How extracted assets are handled during ingestion. */
export enum AssetProcessingMode {
  IGNORE = "ignore",
  STORE = "store",
  DESCRIBE = "describe",
}

export const AssetProcessingModeSchema = z.enum(AssetProcessingMode);

const EntryKindSchema = z.enum(["user_markdown", "image", "convertible", "binary_stub"]);
const EntryOriginSchema = z.enum(["upload", "collection", "extracted"]);
const EntryGeneratedBySchema = z.enum(["user", "converter", "vision", "stub"]);

// ============================================================
// Persisted data schemas (localStorage)
// ============================================================

export const DocumentTabSchema = z.enum(["fetched", "manage"]);
export type DocumentTab = z.infer<typeof DocumentTabSchema>;

export const ExpandedDirsSchema = z.array(z.string());

/** Per-pipeline configuration overrides, keyed by pipeline value. */
export const PipelineConfigsSchema = z.record(z.string(), z.record(z.string(), z.unknown()));

/** User-provided overrides. Sensitive values are not persisted. */
export const UserOverridesSchema = z.object({
  model: z.string(),
  apiKey: z.string(),
  baseUrl: z.string(),
  auxModel: z.string(),
});
export type UserOverrides = z.infer<typeof UserOverridesSchema>;

/** Persisted subset of {@link UserOverrides}: the API key never reaches disk. */
export const PersistedOverridesSchema = UserOverridesSchema.omit({ apiKey: true });
export type PersistedOverrides = z.infer<typeof PersistedOverridesSchema>;

// ============================================================
// API response schemas
// ============================================================

/** Authenticated user information from the backend.
 *
 * Admin status is derived client-side from the fixed `admin` role being
 * present in `roles` — mirrors the server's `User.is_admin` property.
 */
export const UserResponseSchema = z.object({
  id: z.string(),
  email: z.string().nullable().optional(),
  name: z.string().nullable().optional(),
  read_groups: z.array(z.string()).default([]),
  write_groups: z.array(z.string()).default([]),
  roles: z.array(z.string()).default([]),
});
export type UserResponse = z.infer<typeof UserResponseSchema>;

// ============================================================
// Admin response schemas
// ============================================================

export const AdminResetResponseSchema = z.object({
  action: z.string(),
  message: z.string(),
});
export type AdminResetResponse = z.infer<typeof AdminResetResponseSchema>;

export const AdminReindexResponseSchema = z.object({
  stores_reconciled: z.number(),
  message: z.string(),
});
export type AdminReindexResponse = z.infer<typeof AdminReindexResponseSchema>;

export const AdminFactoryResetResponseSchema = z.object({
  actions: z.array(z.string()),
  message: z.string(),
});
export type AdminFactoryResetResponse = z.infer<typeof AdminFactoryResetResponseSchema>;

export const AdminUserInfoSchema = z.object({
  id: z.string(),
  email: z.string().nullable().optional(),
  name: z.string().nullable().optional(),
  document_count: z.number(),
  conversation_count: z.number(),
  has_workspace: z.boolean(),
});
export type AdminUserInfo = z.infer<typeof AdminUserInfoSchema>;

export const AdminListUsersResponseSchema = z.object({
  users: z.array(AdminUserInfoSchema),
});

export const AdminGroupInfoSchema = z.object({
  id: z.string(),
  document_count: z.number(),
  member_count: z.number(),
  has_workspace: z.boolean(),
});
export type AdminGroupInfo = z.infer<typeof AdminGroupInfoSchema>;

export const AdminListGroupsResponseSchema = z.object({
  groups: z.array(AdminGroupInfoSchema),
});

/** Settings exposed by the backend. */
export const BackendSettingsSchema = z.object({
  model: z.string(),
  aux_model: z.string().nullable(),
  has_api_key: z.boolean(),
  base_url: z.string(),
  user: UserResponseSchema,
});
export type BackendSettings = z.infer<typeof BackendSettingsSchema>;

/** Metadata for a conversion pipeline, fetched from the backend. */
export const ConversionPipelineInfoSchema = z.object({
  value: z.string(),
  label: z.string(),
  description: z.string(),
  extensions: z.array(z.string()),
  config_schema: z.record(z.string(), z.unknown()).optional(),
  config_defaults: z.record(z.string(), z.unknown()).optional(),
});
export type ConversionPipelineInfo = z.infer<typeof ConversionPipelineInfoSchema>;

/** Metadata for a chunking pipeline, fetched from the backend. */
export const ChunkingPipelineInfoSchema = z.object({
  value: z.string(),
  label: z.string(),
  description: z.string(),
  config_schema: z.record(z.string(), z.unknown()).optional(),
  config_defaults: z.record(z.string(), z.unknown()).optional(),
});
export type ChunkingPipelineInfo = z.infer<typeof ChunkingPipelineInfoSchema>;

/** A single chunk within a chunked document. */
export const ChunkInfoSchema = z.object({
  text: z.string(),
  token_count: z.number(),
  start_index: z.number(),
  end_index: z.number(),
  start_line: z.number(),
  end_line: z.number(),
});
export type ChunkInfo = z.infer<typeof ChunkInfoSchema>;

/** Response from the chunks endpoint. */
export const ChunkedDocumentResponseSchema = z.object({
  pipeline: z.string(),
  created_at: z.string(),
  chunks: z.array(ChunkInfoSchema),
  entry_kind: EntryKindSchema.optional(),
  stem_path: z.string().nullable().optional(),
  description_path: z.string().nullable().optional(),
  original_path: z.string().nullable().optional(),
  assets_dir: z.string().nullable().optional(),
  origin: EntryOriginSchema.optional(),
  generated_by: EntryGeneratedBySchema.optional(),
  files: z.array(z.string()).optional().default([]),
});
export type ChunkedDocumentResponse = z.infer<typeof ChunkedDocumentResponseSchema>;

export const AssetEntrySchema = z.object({
  name: z.string(),
  path: z.string(),
  description_path: z.string().nullable(),
  description: z.string(),
  size_bytes: z.number(),
  media_type: z.string().nullable().optional(),
});
export type AssetEntry = z.infer<typeof AssetEntrySchema>;

export const AssetListResponseSchema = z.object({
  assets: z.array(AssetEntrySchema),
  assets_dir: z.string(),
});
export type AssetListResponse = z.infer<typeof AssetListResponseSchema>;

export const DocumentInfoSchema = z.object({
  filename: z.string(),
  display_name: z.string(),
  size_bytes: z.number(),
  modified_at: z.string(),
  chunk_count: z.number().nullable().optional(),
  has_original: z.boolean(),
  original_path: z.string().nullable().optional(),
  assets_dir: z.string().nullable().optional(),
  kind: z.enum(["document", "asset"]).optional().default("document"),
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
  content_hash: z.string(),
});
export type DocumentRange = z.infer<typeof DocumentRangeSchema>;

export const GrepLineSchema = z.object({
  line_number: z.number(),
  text: z.string(),
  is_match: z.boolean(),
});
export type GrepLine = z.infer<typeof GrepLineSchema>;

export const GrepMatchSchema = z.object({
  filename: z.string(),
  lines: z.array(GrepLineSchema),
});
export type GrepMatch = z.infer<typeof GrepMatchSchema>;

export const RetrievedChunkSchema = z.object({
  store_key: z.string().nullable().optional(),
  filename: z.string(),
  chunk_index: z.number(),
  text: z.string(),
  token_count: z.number(),
  score: z.number(),
  start_line: z.number(),
  end_line: z.number(),
  start_index: z.number(),
  end_index: z.number(),
  image_path: z.string().nullable().optional(),
});
export type RetrievedChunk = z.infer<typeof RetrievedChunkSchema>;

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

/** A file or directory entry in the document tree (recursive). */
export interface DirectoryEntry {
  type: "file" | "directory";
  name: string;
  path: string;
  size_bytes?: number | null;
  modified_at?: string | null;
  chunk_count?: number | null;
  has_original?: boolean;
  original_path?: string | null;
  assets_dir?: string | null;
  children?: DirectoryEntry[] | null;
}

export const DirectoryEntrySchema: z.ZodType<DirectoryEntry> = z.object({
  type: z.enum(["file", "directory"]),
  name: z.string(),
  path: z.string(),
  size_bytes: z.number().nullable().optional(),
  modified_at: z.string().nullable().optional(),
  chunk_count: z.number().nullable().optional(),
  has_original: z.boolean().optional(),
  original_path: z.string().nullable().optional(),
  assets_dir: z.string().nullable().optional(),
  children: z
    .lazy(() => z.array(DirectoryEntrySchema))
    .nullable()
    .optional(),
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

/** Response from moving a directory. */
export const MoveDirectoryResponseSchema = z.object({
  source: z.string(),
  destination: z.string(),
  files_moved: z.number(),
  message: z.string(),
});
export type MoveDirectoryResponse = z.infer<typeof MoveDirectoryResponseSchema>;

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

/** SSE progress event from streaming collection upload. */
export const CollectionProgressEventSchema = z.object({
  type: z.literal("progress"),
  file: z.string(),
  current: z.number(),
  total: z.number(),
  status: z.enum(["ok", "failed"]),
});
export type CollectionProgressEvent = z.infer<typeof CollectionProgressEventSchema>;

/** SSE completion event from streaming collection upload. */
export const CollectionCompleteEventSchema = CollectionUploadResponseSchema.extend({
  type: z.literal("complete"),
});
export type CollectionCompleteEvent = z.infer<typeof CollectionCompleteEventSchema>;

/** Discriminated union of SSE events from streaming collection upload. */
export const CollectionStreamEventSchema = z.discriminatedUnion("type", [
  CollectionProgressEventSchema,
  CollectionCompleteEventSchema,
]);
export type CollectionStreamEvent = z.infer<typeof CollectionStreamEventSchema>;

/** SSE progress event from a bulk rechunk/reconvert operation. */
export const BulkOperationProgressEventSchema = z.object({
  type: z.literal("progress"),
  file: z.string(),
  current: z.number(),
  total: z.number(),
  status: z.enum(["ok", "failed"]),
});
export type BulkOperationProgressEvent = z.infer<typeof BulkOperationProgressEventSchema>;

/** SSE completion event from a bulk operation. */
export const BulkOperationCompleteEventSchema = z.object({
  type: z.literal("complete"),
  total_files: z.number(),
  failed_files: z.array(z.string()),
  message: z.string(),
});
export type BulkOperationCompleteEvent = z.infer<typeof BulkOperationCompleteEventSchema>;

/** Discriminated union of SSE events from a bulk operation stream. */
export const BulkOperationStreamEventSchema = z.discriminatedUnion("type", [
  BulkOperationProgressEventSchema,
  BulkOperationCompleteEventSchema,
]);
export type BulkOperationStreamEvent = z.infer<typeof BulkOperationStreamEventSchema>;

/** Upload progress state shared between multi-file and collection uploads. */
export interface UploadProgress {
  current: number;
  total: number;
  currentFile: string;
  failedFiles: string[];
}

/** SSE stage event from a single-document operation. */
export const OperationStageEventSchema = z.object({
  type: z.literal("stage"),
  stage: z.string(),
  detail: z.string().default(""),
});
export type OperationStageEvent = z.infer<typeof OperationStageEventSchema>;

/** SSE error event from a single-document operation. */
export const OperationErrorEventSchema = z.object({
  type: z.literal("error"),
  detail: z.string(),
});
export type OperationErrorEvent = z.infer<typeof OperationErrorEventSchema>;

/** SSE completion event for upload/reconvert operations. */
export const UploadCompleteEventSchema = UploadDocumentResponseSchema.extend({
  type: z.literal("complete"),
});
export type UploadCompleteEvent = z.infer<typeof UploadCompleteEventSchema>;

/** Discriminated union of SSE events from a single upload/reconvert stream. */
export const UploadStreamEventSchema = z.discriminatedUnion("type", [
  OperationStageEventSchema,
  OperationErrorEventSchema,
  UploadCompleteEventSchema,
]);
export type UploadStreamEvent = z.infer<typeof UploadStreamEventSchema>;

/** SSE completion event for rechunk operations. */
export const RechunkCompleteEventSchema = z.object({
  type: z.literal("complete"),
  pipeline: z.string(),
  chunk_count: z.number(),
});
export type RechunkCompleteEvent = z.infer<typeof RechunkCompleteEventSchema>;

/** Discriminated union of SSE events from a rechunk stream. */
export const RechunkStreamEventSchema = z.discriminatedUnion("type", [
  OperationStageEventSchema,
  OperationErrorEventSchema,
  RechunkCompleteEventSchema,
]);
export type RechunkStreamEvent = z.infer<typeof RechunkStreamEventSchema>;

/** Current processing stage for a single-document operation. */
export interface OperationStage {
  stage: string;
  detail: string;
}

/** Metadata about an available agent tool. */
export const ToolInfoSchema = z.object({
  name: z.string(),
  description: z.string(),
  group: z.string(),
});
export type ToolInfo = z.infer<typeof ToolInfoSchema>;

/** Agent tool metadata plus the JSON Schema of its call parameters. */
export const ToolSchemaSchema = ToolInfoSchema.extend({
  parameters: z.record(z.string(), z.unknown()),
});
export type ToolSchema = z.infer<typeof ToolSchemaSchema>;

/** Outcome of invoking an agent tool through the debug console. */
export const ToolRunResultSchema = z.object({
  ok: z.boolean(),
  text: z.string().nullable(),
  data: z.unknown().nullable(),
  error: z.string().nullable(),
  elapsed_ms: z.number(),
});
export type ToolRunResult = z.infer<typeof ToolRunResultSchema>;

/** Summary information about a knowledge group. */
export const GroupInfoSchema = z.object({
  slug: z.string(),
  document_count: z.number(),
});
export type GroupInfo = z.infer<typeof GroupInfoSchema>;

/** Response for listing knowledge groups. */
export const GroupListResponseSchema = z.object({
  groups: z.array(GroupInfoSchema),
});
export type GroupListResponse = z.infer<typeof GroupListResponseSchema>;

// ============================================================
// Tool configuration schemas (persisted in localStorage, sent to backend)
// ============================================================

/** OAuth2 Client Credentials configuration for an MCP server. */
export const McpOAuth2ConfigSchema = z.object({
  clientId: z.string(),
  clientSecret: z.string(),
  scopes: z.string().optional(),
});
export type McpOAuth2Config = z.infer<typeof McpOAuth2ConfigSchema>;

/** A user-provided MCP server entry (HTTP transport only). */
export const McpServerEntrySchema = z.object({
  url: z.string(),
  headers: z.record(z.string(), z.string()).default({}),
  toolPrefix: z.string().optional(),
  oauth2: McpOAuth2ConfigSchema.optional(),
});
export type McpServerEntry = z.infer<typeof McpServerEntrySchema>;

/** Bundled tool configuration: disabled built-in tools and custom MCP servers. */
export const ToolsSpecSchema = z.object({
  disabledTools: z.array(z.string()).default([]),
  mcpServers: z.array(McpServerEntrySchema).default([]),
});
export type ToolsSpec = z.infer<typeof ToolsSpecSchema>;

/** Persisted subset of {@link ToolsSpec}: MCP header values and OAuth secrets never reach disk. */
export const PersistedToolsSpecSchema = z.object({
  disabledTools: z.array(z.string()).default([]),
  mcpServers: z.array(McpServerEntrySchema.omit({ headers: true, oauth2: true })).default([]),
});
export type PersistedToolsSpec = z.infer<typeof PersistedToolsSpecSchema>;

// ============================================================
// Frontend-only types (no runtime validation needed)
// ============================================================

/** Position of a chunk within its parent document (discriminated union). */
export type ChunkPosition =
  | { type: "line"; line: number }
  | { type: "line_range"; startLine: number; endLine: number }
  | { type: "full_document" }
  | { type: "web_result"; url: string }
  /** Unlocated span — resolve by searching `FetchedChunk.content` in the document. */
  | { type: "text" };

/** A single fetched chunk (search result, grep match, line range, etc.). */
export interface FetchedChunk {
  id: string;
  filename: string;
  content: string;
  source: string;
  score?: number;
  position: ChunkPosition;
  /**
   * Exact character offsets of the chunk in the original document.
   * Populated for semantic-search chunks so the canvas can highlight
   * the precise span rather than rounding to whole lines.  Never sent
   * to the LLM — citations rely on {@link ChunkPosition} line numbers.
   */
  startIndex?: number;
  endIndex?: number;
}

/** An image binary attached to the model via the read_binary_document tool. */
export interface FetchedImage {
  /** Canonical workspace path of the image, used to fetch the blob. */
  filePath: string;
  mediaType: string;
}

/** A document that groups one or more fetched chunks. */
export interface FetchedDocument {
  filename: string;
  fullContentFetched: boolean;
  fullContent?: string;
  chunkIds: string[];
  bestScore?: number;
  /**
   * Set when the model read an image whose description lives at this
   * document's path (`<stem>.md`); merges the image with its caption.
   */
  image?: FetchedImage;
}

/** Build a deterministic chunk ID from its attributes. */
export function makeChunkId(filename: string, source: string, position: ChunkPosition): string {
  let positionKey: string;
  switch (position.type) {
    case "line":
      positionKey = `line_${position.line}`;
      break;
    case "line_range":
      positionKey = `lines_${position.startLine}_${position.endLine}`;
      break;
    case "full_document":
      positionKey = "full";
      break;
    case "web_result":
      positionKey = "web";
      break;
    case "text":
      positionKey = "text";
      break;
  }
  return `${filename}::${source}::${positionKey}`;
}

/**
 * Numeric sort key for a chunk position.
 * Full document sorts first (-1), then by chunk index / line number.
 */
export function chunkSortKey(position: ChunkPosition): number {
  switch (position.type) {
    case "full_document":
      return -1;
    case "web_result":
      return 0;
    case "line":
      return position.line;
    case "line_range":
      return position.startLine;
    case "text":
      return Number.MAX_SAFE_INTEGER;
  }
}

/** Human-readable label for a chunk position. */
export function chunkPositionLabel(position: ChunkPosition): string {
  switch (position.type) {
    case "line":
      return `Line ${position.line}`;
    case "line_range":
      return `Lines ${position.startLine}-${position.endLine}`;
    case "full_document":
      return "Full document";
    case "web_result": {
      try {
        return new URL(position.url).hostname;
      } catch {
        return position.url;
      }
    }
    case "text":
      return "Cited text";
  }
}

/** Sort chunks by position (full document first, then ascending). */
export function sortChunks(chunks: FetchedChunk[]): FetchedChunk[] {
  return [...chunks].sort((a, b) => chunkSortKey(a.position) - chunkSortKey(b.position));
}

// ============================================================
// Request types (sent to backend, no validation needed)
// ============================================================

export interface SearchDocumentsInput {
  query: string;
  top_k?: number;
}

/** Bundled conversion + chunking pipeline selection with configuration. */
export interface PipelineSpec {
  conversion?: {
    pipeline?: ConversionPipeline;
    config?: Record<string, unknown>;
  };
  chunking?: {
    pipeline?: ChunkingPipeline;
    config?: Record<string, unknown>;
  };
  process_assets?: AssetProcessingMode;
}

/** LLM provider configuration sent to the backend. */
export interface LlmConfig {
  model?: string;
  api_key?: string;
  base_url?: string | null;
}

// ============================================================
// Display constants
// ============================================================

/** Available assistant personalities. */
export type Personality = "default" | "concise" | "detailed" | "structured" | "custom";

/** Zod schema for personality (used in store rehydration). */
export const PersonalitySchema = z.enum(["default", "concise", "detailed", "structured", "custom"]);

/** Personality option for display in UI. */
export interface PersonalityOption {
  value: Personality;
  label: string;
  description: string;
}

/** Available personality options. */
export const PERSONALITY_OPTIONS: PersonalityOption[] = [
  {
    value: "default",
    label: "Default",
    description: "Helpful and accurate with source citations",
  },
  {
    value: "concise",
    label: "Concise",
    description: "Brief, to-the-point responses",
  },
  {
    value: "detailed",
    label: "Detailed",
    description: "Thorough explanations with comprehensive context",
  },
  {
    value: "structured",
    label: "Structured",
    description: "Tables and bullet points instead of prose",
  },
  {
    value: "custom",
    label: "Custom",
    description: "Use a custom system message",
  },
];

/** Agent mode controlling available tools. */
export type AgentMode = "plan" | "execute";

/** Agent mode option for display in UI. */
export interface AgentModeOption {
  value: AgentMode;
  label: string;
}

/** Available agent mode options. */
export const AGENT_MODE_OPTIONS: AgentModeOption[] = [
  { value: "execute", label: "Execute" },
  { value: "plan", label: "Plan" },
];

/**
 * Reasoning effort level for the LLM.
 *
 * Mirrors pydantic-ai's native effort levels plus the `auto` (provider
 * default) and `none` (disabled) sentinels.
 */
export type ReasoningEffort = "auto" | "none" | "minimal" | "low" | "medium" | "high" | "xhigh";

/** Reasoning effort option for display in UI. */
export interface ReasoningEffortOption {
  value: ReasoningEffort;
  label: string;
}

/** Available reasoning effort options. */
export const REASONING_EFFORT_OPTIONS: ReasoningEffortOption[] = [
  { value: "auto", label: "Auto" },
  { value: "none", label: "None" },
  { value: "minimal", label: "Minimal" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Extra High" },
];
