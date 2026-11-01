import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { apiUrl, jsonRequest, requestJson } from "@/api/client";
import { featureFlags } from "@/lib/feature-flags";

/** Metadata about an available agent tool. */
const ToolInfoSchema = z.object({
  name: z.string(),
  description: z.string(),
  group: z.string(),
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

/** OAuth2 Client Credentials configuration for an MCP server. */
const McpOAuth2ConfigSchema = z.object({
  clientId: z.string(),
  clientSecret: z.string(),
  scopes: z.string().optional(),
});
export type McpOAuth2Config = z.infer<typeof McpOAuth2ConfigSchema>;

/** A user-provided MCP server entry (HTTP transport only). */
const McpServerEntrySchema = z.object({
  /** Stable identity of the entry, minted for entries persisted without one. */
  id: z.string().default(() => crypto.randomUUID()),
  url: z.string(),
  headers: z.record(z.string(), z.string()).default({}),
  toolPrefix: z.string().optional(),
  oauth2: McpOAuth2ConfigSchema.optional(),
});
export type McpServerEntry = z.infer<typeof McpServerEntrySchema>;

/** Bundled tool configuration: disabled built-in tools and custom MCP servers. */
const ToolsSpecSchema = z.object({
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

/** The snake_case wire form of an MCP server entry. */
interface McpServerPayload {
  url: string;
  headers: Record<string, string>;
  tool_prefix: string | null;
  oauth2: {
    client_id: string;
    client_secret: string;
    scopes: string | null;
  } | null;
}

/** The snake_case wire form of {@link ToolsSpec}, built by `buildToolsPayload`. */
export interface ToolsPayload {
  disabled_tools: string[];
  mcp_servers: McpServerPayload[];
}

function mcpServerPayload(server: McpServerEntry): McpServerPayload {
  return {
    url: server.url,
    headers: server.headers,
    tool_prefix: server.toolPrefix ?? null,
    oauth2: server.oauth2
      ? {
          client_id: server.oauth2.clientId,
          client_secret: server.oauth2.clientSecret,
          scopes: server.oauth2.scopes ?? null,
        }
      : null,
  };
}

/**
 * Convert a frontend ToolsSpec to the snake_case backend payload.
 *
 * Empty while the {@link featureFlags.toolsSpec} flag is disabled, since tool
 * and MCP customization is a frontend-only feature, so the data then leaves
 * every request without touching the backend.
 */
export function buildToolsPayload(spec: ToolsSpec): ToolsPayload {
  if (!featureFlags.toolsSpec) {
    return { disabled_tools: [], mcp_servers: [] };
  }

  return {
    disabled_tools: spec.disabledTools,
    mcp_servers: spec.mcpServers.map(mcpServerPayload),
  };
}

/** Fetch available agent tools from the backend. */
function listTools(): Promise<ToolInfo[]> {
  return requestJson(apiUrl("/tools"), "fetchTools", z.array(ToolInfoSchema));
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

/** Response from testing an MCP server connection. */
const McpTestResultSchema = z.object({
  ok: z.boolean(),
  tool_count: z.number().nullable(),
  error: z.string().nullable(),
});

/** Test connectivity to an MCP server. */
export function testMcpServer(server: McpServerEntry) {
  const { url, headers, tool_prefix, oauth2 } = mcpServerPayload(server);

  return requestJson(
    apiUrl("/mcp/test"),
    "testMcpServer",
    McpTestResultSchema,
    jsonRequest("POST", { url, headers, tool_prefix, oauth2 }),
  );
}

const all = ["tools"] as const;

// The built-in tools are fixed per server process, so they never go stale.
export const toolQueries = {
  /** The built-in tools a user can switch off. */
  list: () => queryOptions({ queryKey: [...all, "list"], queryFn: listTools, staleTime: "static" }),
  /** The built-in tools with their parameter schemas, for the admin console. */
  schemas: () =>
    queryOptions({ queryKey: [...all, "schemas"], queryFn: listToolSchemas, staleTime: "static" }),
};
