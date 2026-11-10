import { queryOptions, skipToken } from "@tanstack/react-query";
import { z } from "zod";

import { requestJson } from "@/api/client";
import { apiUrl } from "@/lib/health";

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

/** How extracted assets are handled during ingestion. */
export enum AssetProcessingMode {
  IGNORE = "ignore",
  STORE = "store",
  DESCRIBE = "describe",
}

export const ConversionPipelineSchema = z.enum(ConversionPipeline);
export const ChunkingPipelineSchema = z.enum(ChunkingPipeline);
export const AssetProcessingModeSchema = z.enum(AssetProcessingMode);

/** The two pipeline stages a document passes through. */
export type PipelineKind = "conversion" | "chunking";

/** The pipelines each stage offers. */
export interface PipelineByKind {
  conversion: ConversionPipeline;
  chunking: ChunkingPipeline;
}

const PipelineByKindSchemas: { [K in PipelineKind]: z.ZodType<PipelineByKind[K]> } = {
  conversion: ConversionPipelineSchema,
  chunking: ChunkingPipelineSchema,
};

/** Configuration metadata loaded only for the selected pipeline. */
const PipelineConfigInfoSchema = z.object({
  schema: z.record(z.string(), z.unknown()),
  defaults: z.record(z.string(), z.unknown()),
});
type PipelineConfigInfo = z.infer<typeof PipelineConfigInfoSchema>;

/** Bundled conversion and chunking pipeline selection with configuration. */
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

/** List the installed pipelines of one processing stage. */
function listPipelines<K extends PipelineKind>(stage: K): Promise<PipelineByKind[K][]> {
  return requestJson(
    apiUrl(`/pipelines/${stage}`),
    "fetchPipelines",
    z.array(PipelineByKindSchemas[stage]),
  );
}

/** Get configuration metadata for one pipeline of a processing stage. */
function getPipelineConfig(stage: PipelineKind, pipeline: string): Promise<PipelineConfigInfo> {
  return requestJson(
    apiUrl(`/pipelines/${stage}/${encodeURIComponent(pipeline)}/config`),
    "fetchPipelineConfig",
    PipelineConfigInfoSchema,
  );
}

const all = ["pipelines"] as const;

// Pipelines and their configs are fixed per server process, so they never go
// stale. Configs are keyed by stage too, so conversion and chunking cannot
// collide on a shared pipeline name.
export const pipelineQueries = {
  /** The installed pipelines of one stage. */
  list: <K extends PipelineKind>(stage: K) =>
    queryOptions({
      queryKey: [...all, stage],
      queryFn: () => listPipelines(stage),
      staleTime: "static",
    }),
  /**
   * The configuration schema and defaults of *pipeline*, skipped for `null`
   * (the `auto` pipeline takes no configuration).
   */
  config: (stage: PipelineKind, pipeline: string | null) =>
    queryOptions({
      queryKey: [...all, stage, pipeline],
      queryFn: pipeline === null ? skipToken : () => getPipelineConfig(stage, pipeline),
      staleTime: "static",
    }),
};
