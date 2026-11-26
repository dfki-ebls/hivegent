import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { jsonRequest, requestJson } from "@/api/client";
import { apiUrl } from "@/lib/health";

/** An agent tool with the JSON Schema of its call parameters. */
const ToolSchemaSchema = z.object({
  name: z.string(),
  description: z.string(),
  group: z.string(),
  parameters: z.record(z.string(), z.unknown()),
});
type ToolSchema = z.infer<typeof ToolSchemaSchema>;

/** Outcome of invoking an agent tool through the debug console. */
const ToolRunResultSchema = z.object({
  ok: z.boolean(),
  text: z.string().nullable(),
  data: z.unknown().nullable(),
  error: z.string().nullable(),
  elapsed_ms: z.number(),
});
export type ToolRunResult = z.infer<typeof ToolRunResultSchema>;

/** Fetch every agent tool with its parameter JSON Schema (admin only). */
function listToolSchemas(): Promise<ToolSchema[]> {
  return requestJson(apiUrl("/debug/tools"), "fetchToolSchemas", z.array(ToolSchemaSchema));
}

/** Invoke an agent tool with arbitrary arguments (admin only). */
export function runTool(name: string, args: Record<string, unknown>): Promise<ToolRunResult> {
  return requestJson(
    apiUrl(`/debug/tools/${encodeURIComponent(name)}`),
    "runTool",
    ToolRunResultSchema,
    jsonRequest("POST", args),
  );
}

const all = ["tools"] as const;

export const toolQueries = {
  /** The built-in tools with their parameter schemas, for the admin console, fixed per server process. */
  schemas: () =>
    queryOptions({ queryKey: [...all, "schemas"], queryFn: listToolSchemas, staleTime: "static" }),
};
