import { mutationOptions, queryOptions } from "@tanstack/react-query";
import { z } from "zod";

import { jsonRequest, requestJson, requestVoid } from "@/api/client";
import { apiUrl } from "@/lib/health";

const AdminReindexResponseSchema = z.object({ message: z.string() });
type AdminReindexResponse = z.infer<typeof AdminReindexResponseSchema>;

const AdminUserInfoSchema = z.object({
  id: z.string(),
  document_count: z.number(),
  conversation_count: z.number(),
});
export type AdminUserInfo = z.infer<typeof AdminUserInfoSchema>;

const AdminGroupInfoSchema = z.object({
  id: z.string(),
  document_count: z.number(),
});
export type AdminGroupInfo = z.infer<typeof AdminGroupInfoSchema>;

const AdminOverviewSchema = z.object({
  users: z.array(AdminUserInfoSchema),
  groups: z.array(AdminGroupInfoSchema),
});
type AdminOverview = z.infer<typeof AdminOverviewSchema>;

/** What an admin reset wipes, `factory` being workspace and database together. */
type AdminResetTarget = "workspace" | "database" | "factory";

/** State of the server's global maintenance flag. */
const AdminMaintenanceStateSchema = z.object({ enabled: z.boolean() });

/** Wipe the workspace (files and documents), the database (identities and conversations), or both. */
export function adminReset(target: AdminResetTarget): Promise<void> {
  return requestVoid(apiUrl(`/admin/data/${target}`), "adminAction", { method: "DELETE" });
}

/** Reconcile every casebase: re-derive the document index from the files on disk. */
export function adminReindex(): Promise<AdminReindexResponse> {
  return requestJson(apiUrl("/admin/reindex"), "adminAction", AdminReindexResponseSchema, {
    method: "POST",
  });
}

/** The users with a footprint in the local database, and every group. */
function adminOverview(): Promise<AdminOverview> {
  return requestJson(apiUrl("/admin/overview"), "readAdminOverview", AdminOverviewSchema);
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

/** Wipe all data owned by a single user (workspace, SQL, and index). */
export function adminDeleteUserData(userId: string): Promise<void> {
  return requestVoid(apiUrl(`/admin/users/${encodeURIComponent(userId)}/data`), "deleteUserData", {
    method: "DELETE",
  });
}

/** Wipe all data owned by a single group (workspace, SQL, and index). */
export function adminDeleteGroupData(groupId: string): Promise<void> {
  return requestVoid(
    apiUrl(`/admin/groups/${encodeURIComponent(groupId)}/data`),
    "deleteGroupData",
    { method: "DELETE" },
  );
}

const all = ["admin"] as const;

export const adminQueries = {
  /** Every user and group with their data counts, for the admin sections. */
  overview: () => queryOptions({ queryKey: [...all, "overview"], queryFn: adminOverview }),
  /** Whether the server currently rejects everyone but admins. */
  maintenance: () =>
    queryOptions({ queryKey: [...all, "maintenance"], queryFn: adminGetMaintenance }),
};

export const adminMutations = {
  maintenance: () =>
    mutationOptions({
      mutationKey: [...all, "maintenance"],
      mutationFn: adminSetMaintenance,
      meta: { invalidates: [adminQueries.maintenance().queryKey] },
    }),
};
