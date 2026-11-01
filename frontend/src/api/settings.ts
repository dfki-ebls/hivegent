import { queryOptions, useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { apiUrl, MaintenanceError, requestJson } from "@/api/client";
import { featureFlags } from "@/lib/feature-flags";
import { initOidc, useOidc } from "@/oidc";

/**
 * One group the user belongs to, with the permission they hold on it.
 *
 * `id` is what every path and request uses (`@<id>/notes.md`), `name` is a
 * display label only, falling back to the id when the identity provider
 * supplied no display name.
 */
const GroupInfoSchema = z.object({
  id: z.string(),
  name: z.string(),
  writable: z.boolean(),
});
export type GroupInfo = z.infer<typeof GroupInfoSchema>;

/** The authenticated user, whose admin status the fixed `admin` role grants as on the server. */
const UserResponseSchema = z.object({
  id: z.string(),
  email: z.string().nullable().optional(),
  name: z.string().nullable().optional(),
  groups: z.array(GroupInfoSchema).default([]),
  roles: z.array(z.string()).default([]),
});

/** What the chat composer may attach, as the backend defines it. */
const AttachmentLimitsSchema = z.object({
  media_types: z.array(z.string()),
  max_bytes: z.number(),
  /** Images one turn may attach: the model server's per-request cap, or null for none. */
  max_count: z.number().nullable(),
});
type AttachmentLimits = z.infer<typeof AttachmentLimitsSchema>;

const TransparencyConfigSchema = z.object({
  enabled: z.boolean(),
  contact_email: z.string().nullable(),
  minimum_watermark_tokens: z.number(),
});

/** Settings exposed by the backend. */
const BackendSettingsSchema = z.object({
  model: z.string(),
  aux_model: z.string().nullable(),
  stt_model: z.string().nullable(),
  has_api_key: z.boolean(),
  base_url: z.string(),
  user: UserResponseSchema,
  attachments: AttachmentLimitsSchema,
  transparency: TransparencyConfigSchema,
  /** Where RAGold is served, null while the transfer is hidden. */
  ragold_url: z.string().nullable(),
});
type BackendSettings = z.infer<typeof BackendSettingsSchema>;

/** LLM provider configuration sent to the backend. */
export interface LlmConfig {
  model?: string;
  api_key?: string;
  base_url?: string | null;
}

/**
 * Build a sparse LlmConfig from frontend settings.
 *
 * Empty while the {@link featureFlags.llmSpec} flag is disabled, since LLM
 * provider customization is a frontend-only feature, so every request then uses
 * the backend's configured defaults.
 */
export function buildLlmConfig(s: {
  model?: string;
  apiKey?: string;
  baseUrl?: string;
}): LlmConfig {
  if (!featureFlags.llmSpec) return {};

  const config: LlmConfig = {};

  if (s.model) config.model = s.model;
  if (s.apiKey) config.api_key = s.apiKey;
  if (s.baseUrl) config.base_url = s.baseUrl;

  return config;
}

/** LLM config for auxiliary tasks (titles, captions): prefer aux model, fall back to primary. */
export function buildAuxLlmConfig(overrides: {
  model: string;
  auxModel: string;
  apiKey: string;
  baseUrl: string;
}): LlmConfig {
  return buildLlmConfig({
    model: overrides.auxModel || overrides.model,
    apiKey: overrides.apiKey,
    baseUrl: overrides.baseUrl,
  });
}

/** Fetch server-side LLM settings. */
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
const selectIsAdmin = (settings: BackendSettings): boolean =>
  settings.user.roles.includes(ADMIN_ROLE);

/** The current user's backend id, which shares the id space of the admin listings. */
export const selectUserId = (settings: BackendSettings): string => settings.user.id;

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
