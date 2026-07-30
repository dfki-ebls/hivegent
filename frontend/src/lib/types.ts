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
  PLAIN_TEXT = "plain-text",
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

const EntryKindSchema = z.enum(["user_markdown", "image", "video", "convertible", "binary_stub"]);
const EntryOriginSchema = z.enum(["upload", "collection", "extracted", "imported"]);
const EntryGeneratedBySchema = z.enum(["user", "converter", "vision", "stub"]);

// ============================================================
// Persisted data schemas (localStorage / sessionStorage)
// ============================================================

/**
 * Tabs of the document canvas, in display order. Persisted to sessionStorage,
 * so the schema doubles as the allow-list that validates the restored value.
 * Add a future view (e.g. a graph or database panel) by extending this enum
 * and the canvas tab registry — no other wiring required.
 */
export const DocumentCanvasTabSchema = z.enum(["documents", "context"]);
export type DocumentCanvasTab = z.infer<typeof DocumentCanvasTabSchema>;

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
  has_workspace: z.boolean(),
});
export type AdminGroupInfo = z.infer<typeof AdminGroupInfoSchema>;

export const AdminListGroupsResponseSchema = z.object({
  groups: z.array(AdminGroupInfoSchema),
});

/** State of the server's global maintenance flag. */
export const AdminMaintenanceStateSchema = z.object({
  enabled: z.boolean(),
});

/** Settings exposed by the backend. */
export const BackendSettingsSchema = z.object({
  model: z.string(),
  aux_model: z.string().nullable(),
  stt_model: z.string().nullable(),
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
  size_bytes: z.number().nullable().optional(),
  chunks: z.array(ChunkInfoSchema),
  entry_kind: EntryKindSchema.optional(),
  stem_path: z.string().nullable().optional(),
  description_path: z.string().nullable().optional(),
  original_path: z.string().nullable().optional(),
  assets_dir: z.string().nullable().optional(),
  origin: EntryOriginSchema.optional(),
  generated_by: EntryGeneratedBySchema.optional(),
  mime: z.string().nullable().optional(),
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

export const DocumentStatsSchema = z.object({
  line_count: z.number(),
  word_count: z.number(),
  char_count: z.number(),
});
export type DocumentStats = z.infer<typeof DocumentStatsSchema>;

/** Batch document line counts, keyed by the requested workspace path. */
export const DocumentLineCountsResponseSchema = z.object({
  line_counts: z.record(z.string(), z.number()),
});
export type DocumentLineCountsResponse = z.infer<typeof DocumentLineCountsResponseSchema>;

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

/** Response for audio transcription. */
export const TranscriptionResponseSchema = z.object({
  text: z.string(),
});
export type TranscriptionResponse = z.infer<typeof TranscriptionResponseSchema>;

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

/** Lifecycle status of a background job. */
export const JobStatusSchema = z.enum(["queued", "running", "succeeded", "failed", "cancelled"]);
export type JobStatus = z.infer<typeof JobStatusSchema>;

/** Discrete progress of a job (e.g. files processed in a collection). */
export const JobProgressSchema = z.object({
  current: z.number(),
  total: z.number(),
});
export type JobProgress = z.infer<typeof JobProgressSchema>;

/**
 * Snapshot of a background job — the generic shape the `/jobs` feed emits
 * and the job tray renders, independent of which feature submitted it.
 */
export const JobViewSchema = z.object({
  id: z.string(),
  kind: z.string(),
  title: z.string(),
  scope: z.string().nullable(),
  status: JobStatusSchema,
  stage: z.string().nullable(),
  progress: JobProgressSchema.nullable(),
  error: z.string().nullable(),
  created_at: z.number(),
  updated_at: z.number(),
});
export type JobView = z.infer<typeof JobViewSchema>;

/** Marks the end of the job feed's initial replay (see backend `FeedReady`). */
export const FeedReadySchema = z.object({ type: z.literal("ready") });

/** A job feed event: either a job snapshot or the seed-complete marker. */
export const FeedEventSchema = z.union([JobViewSchema, FeedReadySchema]);
export type FeedEvent = z.infer<typeof FeedEventSchema>;

/** Terminal job statuses — no further updates will arrive. */
export const TERMINAL_JOB_STATUSES: ReadonlySet<JobStatus> = new Set([
  "succeeded",
  "failed",
  "cancelled",
]);

/** Active job statuses — work is queued or in progress (the complement of terminal). */
export const ACTIVE_JOB_STATUSES: ReadonlySet<JobStatus> = new Set(["queued", "running"]);

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

/**
 * Where a fetched chunk came from: the model tools (read/grep/search/web) plus
 * the UI-only origins (preview fetch, citation marker).
 */
export type ChunkOrigin = "read" | "grep" | "search" | "web" | "preview" | "citation";

/** A single fetched chunk (search result, grep match, line range, etc.). */
export interface FetchedChunk {
  id: string;
  filename: string;
  content: string;
  /** Where the chunk came from; drives the origin badge. */
  origin: ChunkOrigin;
  /** Optional query/pattern for display (grep pattern, search/web query). */
  detail?: string;
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
  /**
   * Total line count of the document, recorded once from whichever source
   * learns it first: a partial read's `total_lines` or the fetched full
   * content.  The single denominator for the coverage map, so a partial read
   * doesn't render as full coverage.
   */
  totalLines?: number;
  chunkIds: string[];
  /**
   * Set when the model read an image whose description lives at this
   * document's path (`<stem>.md`); merges the image with its caption.
   */
  image?: FetchedImage;
}

/** Build a deterministic chunk ID from its attributes. */
export function makeChunkId(
  filename: string,
  origin: ChunkOrigin,
  detail: string | undefined,
  position: ChunkPosition,
): string {
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

  const originKey = detail ? `${origin}:${detail}` : origin;

  return `${filename}::${originKey}::${positionKey}`;
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

/** Human-readable label for a chunk's origin ("grep: foo", "read"). */
export function chunkOriginLabel({
  origin,
  detail,
}: Pick<FetchedChunk, "origin" | "detail">): string {
  return detail ? `${origin}: ${detail}` : origin;
}

/** The line-based `ChunkPosition` variants a citation `line` attribute yields. */
export type LinePosition = Extract<ChunkPosition, { type: "line" | "line_range" }>;

/**
 * Whether a position carries concrete line numbers, the single unit the
 * coverage map can place.  The one definition of "mappable", shared by the map
 * builder and the line-count backfill so they never disagree about which
 * documents get a map.
 */
export function isLinePosition(position: ChunkPosition): position is LinePosition {
  return position.type === "line" || position.type === "line_range";
}

/**
 * Parse a citation `line` attribute ("42", "42,46", "50-55", "42,50-55,90")
 * into canonical line positions, skipping malformed tokens.
 */
export function parseLinePositions(line: string | undefined): LinePosition[] {
  if (!line) return [];
  const positions: LinePosition[] = [];
  for (const token of line.split(",")) {
    const range = /^\s*(\d+)\s*-\s*(\d+)\s*$/.exec(token);
    if (range) {
      const startLine = Number(range[1]);
      const endLine = Number(range[2]);
      if (startLine >= 1 && endLine >= startLine) {
        positions.push({ type: "line_range", startLine, endLine });
      }
      continue;
    }
    const single = /^\s*(\d+)\s*$/.exec(token);
    if (single && Number(single[1]) >= 1) positions.push({ type: "line", line: Number(single[1]) });
  }
  return positions;
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
 * Mirrors pydantic-ai's native effort levels plus the `auto` (a stable alias
 * for the deployed default effort) and `none` (disabled) sentinels. `auto` is
 * the default and resolves server-side to the deployed default effort, so that
 * default can be retargeted without a client change.
 */
export type ReasoningEffort = "auto" | "none" | "minimal" | "low" | "medium" | "high" | "xhigh";

/** Reasoning effort option for display in UI. */
export interface ReasoningEffortOption {
  value: ReasoningEffort;
  label: string;
}

/** Selectable reasoning effort options: `auto`, `none`, and the 1:1 pydantic-ai levels. */
export const REASONING_EFFORT_OPTIONS: ReasoningEffortOption[] = [
  { value: "auto", label: "Auto" },
  { value: "none", label: "None" },
  { value: "minimal", label: "Minimal" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Extra High" },
];
