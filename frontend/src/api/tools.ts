import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { jsonRequest, requestJson } from "@/api/client";
import { apiUrl } from "@/lib/health";

/** Metadata about an available agent tool. */
const ToolInfoSchema = z.object({
  name: z.string(),
  description: z.string(),
  group: z.string(),
  /** Whether a list of withheld tools may name it. */
  switchable: z.boolean().default(true),
});
type ToolInfo = z.infer<typeof ToolInfoSchema>;

/** Agent tool metadata plus the JSON Schema of its call parameters. */
const ToolSchemaSchema = ToolInfoSchema.extend({
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

/** Fetch the agent tools a user can switch off, which a list of withheld tools may name. */
async function listTools(): Promise<ToolInfo[]> {
  const tools = await requestJson(apiUrl("/tools"), "fetchTools", z.array(ToolInfoSchema));

  return tools.filter((tool) => tool.switchable);
}

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
  /** The tools a user can switch off, an external server's only while it is connected. */
  list: () => queryOptions({ queryKey: [...all, "list"], queryFn: listTools }),
  /** The built-in tools with their parameter schemas, for the admin console, fixed per server process. */
  schemas: () =>
    queryOptions({ queryKey: [...all, "schemas"], queryFn: listToolSchemas, staleTime: "static" }),
};
