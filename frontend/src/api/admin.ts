import { mutationOptions, queryOptions, type QueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { jsonRequest, requestJson, requestVoid } from "@/api/client";
import { apiUrl } from "@/lib/health";
import { onSourcesStale } from "@/stores/jobs-store";

const AdminReindexResponseSchema = z.object({ message: z.string() });
type AdminReindexResponse = z.infer<typeof AdminReindexResponseSchema>;

/** A user who stored something, with what they hold. */
const AdminUserInfoSchema = z.object({
  id: z.string(),
  document_count: z.number(),
  conversation_count: z.number(),
});

/** A group whose workspace or library was used, with what it holds. */
const AdminGroupInfoSchema = z.object({
  id: z.string(),
  document_count: z.number(),
});

const AdminOverviewSchema = z.object({
  users: z.array(AdminUserInfoSchema),
  groups: z.array(AdminGroupInfoSchema),
});
type AdminOverview = z.infer<typeof AdminOverviewSchema>;

/** The users and groups that stored something, membership living in the token alone. */
function adminOverview(): Promise<AdminOverview> {
  return requestJson(apiUrl("/admin/overview"), "readAdminOverview", AdminOverviewSchema);
}

/** State of the server's global maintenance flag. */
const AdminMaintenanceStateSchema = z.object({ enabled: z.boolean() });

/** Reconcile every workspace: re-derive the document index from the files on disk. */
export function adminReindex(): Promise<AdminReindexResponse> {
  return requestJson(apiUrl("/admin/reindex"), "adminAction", AdminReindexResponseSchema, {
    method: "POST",
  });
}

/** Read the server's global maintenance flag. */
async function adminGetMaintenance(): Promise<boolean> {
  return (
    await requestJson(apiUrl("/admin/maintenance"), "readMaintenance", AdminMaintenanceStateSchema)
  ).enabled;
}

/** Set the server's global maintenance flag (persisted across restarts). */
async function adminSetMaintenance(enabled: boolean): Promise<boolean> {
  return (
    await requestJson(
      apiUrl("/admin/maintenance"),
      "setMaintenance",
      AdminMaintenanceStateSchema,
      jsonRequest("PUT", { enabled }),
    )
  ).enabled;
}

/** What a tool's call changes, which decides its modes and approvals. */
const EffectSchema = z.enum(["read", "write", "execute"]);

const ToolsSchema = z.record(z.string(), EffectSchema);

/** A configured external MCP server and the effect of each tool it offers, without secrets. */
export const McpIntegrationSchema = z.object({
  kind: z.literal("mcp"),
  id: z.string(),
  host: z.string(),
  groups: z.array(z.string()),
  trusted: z.boolean(),
  connected: z.boolean(),
  error: z.string().nullable(),
  tools: ToolsSchema,
});

/** A configured SQL database or OpenAPI API and the effect of each tool it offers, without secrets. */
export const DataIntegrationSchema = z.object({
  kind: z.enum(["database", "api"]),
  id: z.string(),
  host: z.string(),
  groups: z.array(z.string()),
  tools: ToolsSchema,
});

const IntegrationSchema = z.discriminatedUnion("kind", [
  McpIntegrationSchema,
  DataIntegrationSchema,
]);
export type Integration = z.infer<typeof IntegrationSchema>;

/** List the MCP servers, databases, and APIs the operator configured. */
function adminListIntegrations(): Promise<Integration[]> {
  return requestJson(apiUrl("/admin/integrations"), "listIntegrations", z.array(IntegrationSchema));
}

/** An enabled plugin and every id it contributes, each tool with its effect. */
export const PluginInfoSchema = z.object({
  name: z.string(),
  features: z.array(z.object({ id: z.string(), tools: ToolsSchema })),
  converters: z.array(z.string()),
  chunkers: z.array(z.string()),
  skills: z.array(z.string()),
});
type PluginInfo = z.infer<typeof PluginInfoSchema>;

function adminListPlugins(): Promise<PluginInfo[]> {
  return requestJson(apiUrl("/admin/plugins"), "listPlugins", z.array(PluginInfoSchema));
}

/** A configured external source and how its last sync went, without secrets. */
const SourceStatusSchema = z.object({
  id: z.string(),
  connector: z.string(),
  host: z.string(),
  groups: z.array(z.string()),
  interval_minutes: z.number(),
  requested: z.enum(["sync", "confirm"]).nullable(),
  running: z.boolean(),
  done: z.number(),
  total: z.number(),
  synced_at: z.string().nullable(),
  error: z.string().nullable(),
  held_deletes: z.number(),
  items: z.number(),
});
export type SourceStatus = z.infer<typeof SourceStatusSchema>;

/** List the external sources the operator configured. */
function adminListSources(): Promise<SourceStatus[]> {
  return requestJson(apiUrl("/admin/sources"), "listSources", z.array(SourceStatusSchema));
}

/** Request a sync of a source, which a process runs soon and the feed reports, `confirmDeletes` deleting what its brake held. */
function adminSyncSource({
  id,
  confirmDeletes = false,
}: {
  id: string;
  confirmDeletes?: boolean;
}): Promise<void> {
  const query = new URLSearchParams({ confirm_deletes: String(confirmDeletes) });

  return requestVoid(
    apiUrl(`/admin/sources/${encodeURIComponent(id)}/sync?${query}`),
    "syncSource",
    { method: "POST" },
  );
}

/** Refetch the sources of *client* whenever the feed says one's sync state may have changed. Returns the unsubscribe. */
export function syncSourceQueries(client: QueryClient): () => void {
  return onSourcesStale(
    () => void client.invalidateQueries({ queryKey: adminQueries.sources().queryKey }),
  );
}

const all = ["admin"] as const;

export const adminQueries = {
  /** The users and groups with local data and their counts. */
  overview: () => queryOptions({ queryKey: [...all, "overview"], queryFn: adminOverview }),
  /** Whether the server currently rejects everyone but admins. */
  maintenance: () =>
    queryOptions({ queryKey: [...all, "maintenance"], queryFn: adminGetMaintenance }),
  /** The configured MCP servers, databases, and APIs with their tools. */
  integrations: () =>
    queryOptions({ queryKey: [...all, "integrations"], queryFn: adminListIntegrations }),
  /** The configured external sources and how their last sync went. */
  sources: () => queryOptions({ queryKey: [...all, "sources"], queryFn: adminListSources }),
  /** The enabled plugins, fixed per server process. */
  plugins: () =>
    queryOptions({
      queryKey: [...all, "plugins"],
      queryFn: adminListPlugins,
      staleTime: "static",
    }),
};

export const adminMutations = {
  maintenance: () =>
    mutationOptions({
      mutationKey: [...all, "maintenance"],
      mutationFn: adminSetMaintenance,
      meta: { invalidates: [adminQueries.maintenance().queryKey] },
    }),
  syncSource: () =>
    mutationOptions({
      mutationKey: [...all, "sources", "sync"],
      mutationFn: adminSyncSource,
      meta: { invalidates: [adminQueries.sources().queryKey] },
    }),
};
