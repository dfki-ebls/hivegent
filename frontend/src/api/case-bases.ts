import { mutationOptions, queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { encodeFilePath, jsonRequest, requestJson } from "@/api/client";
import { apiUrl } from "@/lib/health";
import { type Json, type JsonObject } from "@/lib/similarity";

const JsonObjectSchema = z.record(z.string(), z.json());

/** A case base with the configuration inferred from its data that a query is ranked by (backend `CaseBaseResponse`). */
export const CaseBaseSchema = z.object({
  path: z.string(),
  count: z.number(),
  graphs: z.boolean(),
  config: JsonObjectSchema,
  query_schema: JsonObjectSchema,
});
export type CaseBase = z.infer<typeof CaseBaseSchema>;

/** Why a case matched, by attribute for records and by node mapping for graphs (cbrkit `Explanation`). */
export interface Explanation {
  similarity: number;
  query: Json;
  case: Json;
  weight: number | null;
  contribution: number | null;
  attributes: Record<string, Explanation>;
  graph: GraphExplanation | null;
}

interface GraphExplanation {
  exact: boolean;
  nodes: Explanation[];
  unmapped_nodes: Json[];
  node_coverage: number;
  edge_coverage: number;
}

const ExplanationSchema: z.ZodType<Explanation> = z.lazy(() =>
  z.object({
    similarity: z.number(),
    query: z.json(),
    case: z.json(),
    weight: z.number().nullable(),
    contribution: z.number().nullable(),
    attributes: z.record(z.string(), ExplanationSchema),
    graph: z
      .object({
        exact: z.boolean(),
        nodes: z.array(ExplanationSchema),
        unmapped_nodes: z.array(z.json()),
        node_coverage: z.number(),
        edge_coverage: z.number(),
      })
      .nullable(),
  }),
);

/** A ranked case with its similarity as its score (backend `CaseMatch`, a cbrkit `Hit`). */
const CaseMatchSchema = z.object({
  key: z.union([z.string(), z.number()]),
  score: z.number(),
  value: z.json(),
  explanation: ExplanationSchema,
});
export type CaseMatch = z.infer<typeof CaseMatchSchema>;

const CaseQueryResponseSchema = z.object({
  matches: z.array(CaseMatchSchema),
  query: z.json(),
});

/** An example to rank by, exactly one of the three. */
export interface CaseQuery {
  query?: JsonObject;
  query_path?: string;
  query_key?: string;
  limit: number;
}

const caseBaseUrl = (path: string, action?: "query") =>
  apiUrl(`/case-bases/${action ? `${action}/` : ""}${encodeFilePath(path)}`);

/** Under the workspace, so the feed refreshes a case base whenever its workspace changed. */
const all = ["workspace", "case-base"] as const;

export const caseBaseQueries = {
  /** The case base at *path*, its configuration inferred in memory. */
  detail: (path: string) =>
    queryOptions({
      queryKey: [...all, path],
      queryFn: () => requestJson(caseBaseUrl(path), "fetchCaseBase", CaseBaseSchema),
    }),
};

export const caseBaseMutations = {
  query: () =>
    mutationOptions({
      mutationKey: [...all, "query"],
      mutationFn: ({ path, query }: { path: string; query: CaseQuery }) =>
        requestJson(
          caseBaseUrl(path, "query"),
          "queryCaseBase",
          CaseQueryResponseSchema,
          jsonRequest("POST", query),
        ),
    }),
};
