/**
 * Shared types and Zod schemas.
 *
 * All data that crosses trust boundaries (API responses, localStorage) has a
 * Zod schema for runtime validation.  TypeScript types are derived from schemas
 * with `z.infer` wherever possible.  Plain interfaces are kept only for
 * frontend-only structures and outgoing request payloads.
 */

import { z } from "zod";
import type { ChatMessage } from "@/lib/chat/chat-utils";

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
  ANYDOC = "anydoc",
  PDF_INSPECTOR = "pdf-inspector",
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

/** One group the user belongs to, with the permission they hold on it.
 *
 * `id` is what every path and request uses (`@<id>/notes.md`); `name` is a
 * display label only, falling back to the id when the identity provider
 * supplied no display name.
 */
export const GroupInfoSchema = z.object({
  id: z.string(),
  name: z.string(),
  writable: z.boolean(),
});
export type GroupInfo = z.infer<typeof GroupInfoSchema>;

/** Authenticated user information from the backend.
 *
 * Admin status is derived client-side from the fixed `admin` role being
 * present in `roles` — mirrors the server's `User.is_admin` property.
 */
export const UserResponseSchema = z.object({
  id: z.string(),
  email: z.string().nullable().optional(),
  name: z.string().nullable().optional(),
  groups: z.array(GroupInfoSchema).default([]),
  roles: z.array(z.string()).default([]),
});
export type UserResponse = z.infer<typeof UserResponseSchema>;

export const ScratchClearedResponseSchema = z.object({
  files_removed: z.number(),
});
export type ScratchClearedResponse = z.infer<typeof ScratchClearedResponseSchema>;

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

/**
 * Conversation export/import interchange, mirroring `hivegent.types`.
 *
 * Plain interfaces rather than schemas: the halves carry `ChatMessage` arrays,
 * which have no Zod schema anywhere (see `getConversationMessages`), and the
 * archive is passed through to a download rather than driving any UI.
 */

/** One composed system prompt and the messages sent under it. */
export interface InstructionsSnapshot {
  message_ids: string[];
  text: string;
}

/** A conversation's active path as the database holds it, with its prompts. */
export interface ServerConversation {
  id: string | null;
  title: string | null;
  messages: ChatMessage[];
  instructions: InstructionsSnapshot[];
}

/** A conversation exactly as the browser tab held it, errors included. */
export interface ClientConversation {
  id: string;
  title: string | null;
  exported_at: string;
  error: string | null;
  messages: ChatMessage[];
}

/** Both halves of an exported conversation; either may be absent. */
export interface ConversationArchive {
  backend: ServerConversation | null;
  frontend: ClientConversation | null;
}

/** What the chat composer may attach, as the backend defines it. */
export const AttachmentLimitsSchema = z.object({
  media_types: z.array(z.string()),
  max_bytes: z.number(),
});
export type AttachmentLimits = z.infer<typeof AttachmentLimitsSchema>;

/** Settings exposed by the backend. */
export const BackendSettingsSchema = z.object({
  model: z.string(),
  aux_model: z.string().nullable(),
  stt_model: z.string().nullable(),
  has_api_key: z.boolean(),
  base_url: z.string(),
  user: UserResponseSchema,
  attachments: AttachmentLimitsSchema,
});
export type BackendSettings = z.infer<typeof BackendSettingsSchema>;

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

/** Configuration metadata loaded only for the selected pipeline. */
export const PipelineConfigInfoSchema = z.object({
  schema: z.record(z.string(), z.unknown()),
  defaults: z.record(z.string(), z.unknown()),
});
export type PipelineConfigInfo = z.infer<typeof PipelineConfigInfoSchema>;

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

/** A scope changed through work that ran inline, so never was a job. */
export const ScopeChangedSchema = z.object({
  type: z.literal("scope-changed"),
  scope: z.string(),
});
export type ScopeChanged = z.infer<typeof ScopeChangedSchema>;

/** A job feed event: a job snapshot, the seed-complete marker, or a change. */
export const FeedEventSchema = z.union([JobViewSchema, FeedReadySchema, ScopeChangedSchema]);
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
export type ChunkOrigin = "read" | "grep" | "search" | "web" | "preview";

/** A single fetched chunk from a persisted tool result. */
export interface FetchedChunk {
  id: string;
  filename: string;
  content: string;
  /** Where the chunk came from; drives the origin badge. */
  origin: ChunkOrigin;
  /** Optional query/pattern for display (grep pattern, search/web query). */
  detail?: string;
  /** Tool call that captured this evidence, keeping repeated reads distinct. */
  sourceId?: string;
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
  sourceId?: string,
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

  return `${filename}::${detail ? `${origin}:${detail}` : origin}::${positionKey}${
    sourceId ? `::${sourceId}` : ""
  }`;
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

/** The inclusive `[start, end]` line span a line position covers. */
export function lineBounds(position: LinePosition): [number, number] {
  return position.type === "line"
    ? [position.line, position.line]
    : [position.startLine, position.endLine];
}

/**
 * Parse a citation `line` attribute ("42", "42,46", "50-55", "42,50-55,90")
 * into canonical line positions, skipping malformed tokens.
 */
export function parseLinePositions(line: string | undefined): LinePosition[] {
  if (!line) return [];
  const positions: LinePosition[] = [];
  for (const token of line.split(",")) {
    const match = /^\s*(\d+)(?:\s*-\s*(\d+))?\s*$/.exec(token);
    if (!match) continue;
    const startLine = Number(match[1]);
    const endLine = Number(match[2] ?? match[1]);
    if (startLine < 1 || endLine < startLine) continue;
    positions.push(
      match[2] ? { type: "line_range", startLine, endLine } : { type: "line", line: startLine },
    );
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

/**
 * The half of a chat request that composes the model's prompt prefix, mirroring
 * `AgentRunConfig` on the backend.
 *
 * Compaction posts exactly this beside its messages, because the summary is
 * asked for as one more turn of the conversation and only reuses the provider's
 * cached prefix while every field here matches the turns before it.
 */
export interface AgentRunConfig {
  personality: Personality;
  system_message?: string;
  mode: AgentMode;
  llm: LlmConfig;
  included_documents: string[];
  excluded_documents: string[];
  tools: ToolsPayload;
}

/** The snake_case wire form of {@link ToolsSpec}, built by `buildToolsPayload`. */
export interface ToolsPayload {
  disabled_tools: string[];
  mcp_servers: {
    url: string;
    headers: Record<string, string>;
    tool_prefix: string | null;
    oauth2: {
      client_id: string;
      client_secret: string;
      scopes: string | null;
    } | null;
  }[];
}

/**
 * {@link AgentRunConfig} plus the model setting only a chat turn carries.
 *
 * Narrower than the backend class of the same name, which also holds
 * `conversation_id`, `trigger`, and `message_id`: those address a turn rather
 * than configure it, and `useHivegentChat` splices them into the body itself.
 */
export interface ChatRequestConfig extends AgentRunConfig {
  reasoning_effort: ReasoningEffort;
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

/**
 * Agent mode controlling which tools are offered and how writes are gated.
 *
 * `interactive` (the default) offers the write tools but asks for confirmation
 * before every call, `read` withholds them entirely, and `write` runs them
 * unattended. `plan` is `read` plus the planning tool, so the agent drafts a
 * plan for the user to approve instead of acting.
 */
export type AgentMode = "interactive" | "read" | "write" | "plan";

/** Agent mode option for display in UI. */
export interface AgentModeOption {
  value: AgentMode;
  label: string;
}

/** Available agent mode options. */
export const AGENT_MODE_OPTIONS: AgentModeOption[] = [
  { value: "interactive", label: "Interactive" },
  { value: "read", label: "Read" },
  { value: "write", label: "Write" },
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
