import { queryOptions, useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { MaintenanceError, requestJson } from "@/api/client";
import { REASONING_EFFORTS } from "@/api/conversations";
import { apiUrl } from "@/lib/health";
import { initOidc, useOidc } from "@/oidc";

/**
 * One workspace the user sees, with the grant the server derived for it.
 *
 * `scope` is the prefix every path and request uses (`~`, `@<id>`, `/mnt/<id>`,
 * the global one), `name` a group's display label or a source's id, empty for
 * the personal and the global workspace, which the client names itself.
 */
const WorkspaceInfoSchema = z.object({
  scope: z.string(),
  kind: z.enum(["global", "group", "user", "source"]),
  name: z.string(),
  writable: z.boolean(),
});
export type WorkspaceInfo = z.infer<typeof WorkspaceInfoSchema>;

/** The authenticated user, whose admin status the fixed `admin` role grants as on the server. */
const UserResponseSchema = z.object({
  id: z.string(),
  email: z.string().nullable().optional(),
  name: z.string().nullable().optional(),
  workspaces: z.array(WorkspaceInfoSchema).default([]),
  roles: z.array(z.string()).default([]),
});

/** What the chat composer may attach, as the backend defines it. */
const AttachmentLimitsSchema = z.object({
  media_types: z.array(z.string()),
  max_bytes: z.number(),
});
type AttachmentLimits = z.infer<typeof AttachmentLimitsSchema>;

const TransparencyConfigSchema = z.object({
  enabled: z.boolean(),
  contact_email: z.string().nullable(),
  minimum_watermark_tokens: z.number(),
});

/** A chat model of the operator's catalog, with what the composer offers for it. */
const ModelInfoSchema = z.object({
  id: z.string(),
  label: z.string(),
  vision: z.boolean(),
  /** Images one turn may attach: its server's per-request cap, or null for none. */
  max_images: z.number().nullable(),
  /** The reasoning efforts it offers, none for a model that does not reason. */
  reasoning: z.array(z.enum(REASONING_EFFORTS)),
  context_window: z.number().nullable(),
});
export type ModelInfo = z.infer<typeof ModelInfoSchema>;

/** Settings exposed by the backend. */
export const BackendSettingsSchema = z.object({
  models: z.array(ModelInfoSchema),
  /** The model a request naming none runs on, null without a catalog. */
  default_model: z.string().nullable(),
  /** Whether the speech input fallback can transcribe audio. */
  transcription: z.boolean(),
  user: UserResponseSchema,
  attachments: AttachmentLimitsSchema,
  transparency: TransparencyConfigSchema,
  /** Where RAGold is served, null while the transfer is hidden. */
  ragold_url: z.string().nullable(),
  /** Whether the debugging consoles serve the user, which only an operator enables. */
  debug: z.boolean(),
});
export type BackendSettings = z.infer<typeof BackendSettingsSchema>;

/** Fetch what the server offers and the current user. */
export function getSettings(): Promise<BackendSettings> {
  return requestJson(apiUrl("/settings"), "fetchSettings", BackendSettingsSchema);
}

const all = ["settings"] as const;

export const settingsQueries = {
  /**
   * The backend's readiness, its runtime config, and the OIDC client built
   * from it. `initOidc` caches its promise, so the query only tracks it.
   */
  bootstrap: () =>
    queryOptions({
      queryKey: [...all, "bootstrap"],
      queryFn: () => initOidc().then(() => true),
      staleTime: "static",
      retry: false,
    }),
  /**
   * The server's defaults and the current user, fixed for the session.
   *
   * It retries forever, so during maintenance it doubles as the recovery poll
   * and the app loads by itself once an admin turns the mode back off.
   */
  server: () =>
    queryOptions({
      queryKey: [...all, "server"],
      queryFn: getSettings,
      staleTime: "static",
      retry: true,
      retryDelay: (_, error) => (error instanceof MaintenanceError ? 5000 : 1000),
    }),
};

/**
 * Read the server settings, through *select* if given, `undefined` until they
 * load. They are only requested for a logged-in user, whom they describe.
 */
export function useServerSettings<T = BackendSettings>(
  select?: (settings: BackendSettings) => T,
): T | undefined {
  const { isUserLoggedIn } = useOidc();

  return useQuery({ ...settingsQueries.server(), select, enabled: isUserLoggedIn }).data;
}

/** The fixed role name that grants administrator privileges. */
const ADMIN_ROLE = "admin";

/** Whether the user is an administrator, the same rule as the server's `User.is_admin`. */
export const selectIsAdmin = (settings: BackendSettings): boolean =>
  settings.user.roles.includes(ADMIN_ROLE);

/**
 * The constraints the composer enforces on attachments. Undefined until the
 * settings load, a sub-second window in which the composer does not filter and
 * the server rejects what it must.
 */
export const selectAttachmentLimits = (settings: BackendSettings): AttachmentLimits =>
  settings.attachments;

/** Whether the user is an administrator, `false` until the settings load. */
export function useIsAdmin(): boolean {
  return useServerSettings(selectIsAdmin) ?? false;
}

/** Whether the debugging consoles serve the user, `false` until the settings load. */
export function useDebugConsoles(): boolean {
  return useServerSettings((settings) => settings.debug) ?? false;
}
