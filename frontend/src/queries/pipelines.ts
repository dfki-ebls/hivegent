import { queryOptions, skipToken } from "@tanstack/react-query";

import { getPipelineConfig, listPipelines } from "@/lib/api";
import type { PipelineKind } from "@/lib/types";

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
