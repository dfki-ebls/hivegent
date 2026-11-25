import { createFileRoute, notFound, Outlet } from "@tanstack/react-router";

import { settingsQueries } from "@/api/settings";
import { enforceLogin } from "@/oidc";

/** The debugging consoles, which exist only where the operator enabled them for admins. */
export const Route = createFileRoute("/debug")({
  beforeLoad: async (options) => {
    await enforceLogin(options);
    const settings = await options.context.queryClient.query(settingsQueries.server());

    if (!settings.debug) throw notFound();
  },
  component: Outlet,
});
