import { queryOptions, useQuery } from "@tanstack/react-query";

import { getSettings, MaintenanceError } from "@/lib/api";
import type { AttachmentLimits, BackendSettings } from "@/lib/types";
import { initOidc, useOidc } from "@/oidc";

const all = ["settings"] as const;

export const settingsQueries = {
  /**
   * The backend's readiness, its runtime config, and the OIDC client built
   * from it.  `initOidc` caches its promise, so the query only tracks it.
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
 * load.  They are only requested for a logged-in user, whom they describe.
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
 * The current user's backend id, sharing the id space of the admin listings,
 * so it can filter the caller out of self-targeting lists.
 */
export const selectUserId = (settings: BackendSettings): string => settings.user.id;

/**
 * The constraints the composer enforces on attachments.  Undefined until the
 * settings load, a sub-second window in which the composer simply does not
 * filter and the server rejects what it must.
 */
export const selectAttachmentLimits = (settings: BackendSettings): AttachmentLimits =>
  settings.attachments;

/** Whether the user is an administrator, `false` until the settings load. */
export function useIsAdmin(): boolean {
  return useServerSettings(selectIsAdmin) ?? false;
}
