import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { requestJson } from "@/api/client";
import { apiUrl } from "@/lib/health";

/** An installed pipeline, labelled in the interface language by the server. */
const PipelineInfoSchema = z.object({
  id: z.string(),
  label: z.string(),
  description: z.string(),
});
type PipelineInfo = z.infer<typeof PipelineInfoSchema>;

/** The two pipeline stages a document passes through. */
export type PipelineKind = "conversion" | "chunking";

/** List the installed pipelines of one processing stage, `auto` first. */
function listPipelines(stage: PipelineKind): Promise<PipelineInfo[]> {
  return requestJson(
    apiUrl(`/admin/pipelines/${stage}`),
    "fetchPipelines",
    z.array(PipelineInfoSchema),
  );
}

const all = ["pipelines"] as const;

// Pipelines are fixed per server process, so they never go stale.
export const pipelineQueries = {
  /** The installed pipelines of one stage. */
  list: (stage: PipelineKind) =>
    queryOptions({
      queryKey: [...all, stage],
      queryFn: () => listPipelines(stage),
      staleTime: "static",
    }),
};
