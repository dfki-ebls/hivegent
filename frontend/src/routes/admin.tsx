import { createFileRoute, notFound } from "@tanstack/react-router";
import {
  BlocksIcon,
  BotIcon,
  CloudIcon,
  LayoutDashboardIcon,
  PlugIcon,
  SparklesIcon,
  WorkflowIcon,
  WrenchIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { selectIsAdmin, settingsQueries } from "@/api/settings";
import { SettingsShell } from "@/components/settings/settings-shell";
import { keyPrefix } from "@/i18n";
import { enforceLogin } from "@/oidc";

const ADMIN_T = keyPrefix(($) => $.settings.admin);

/**
 * What the operator configured, read-only, for admins alone. The server
 * decides who is one and refuses everyone else, so this gate only spares the rest
 * a page of failed requests.
 */
export const Route = createFileRoute("/admin")({
  beforeLoad: async (options) => {
    await enforceLogin(options);
    const settings = await options.context.queryClient.query(settingsQueries.server());

    if (!selectIsAdmin(settings)) throw notFound();
  },
  component: AdminLayout,
});

function AdminLayout() {
  const { t } = useTranslation(undefined, ADMIN_T);

  return (
    <SettingsShell
      title={t(($) => $.title)}
      note={t(($) => $.readOnly)}
      pages={[
        { to: "/admin/overview", label: t(($) => $.nav.overview), icon: LayoutDashboardIcon },
        { to: "/admin/models", label: t(($) => $.nav.models), icon: BotIcon },
        { to: "/admin/plugins", label: t(($) => $.nav.plugins), icon: BlocksIcon },
        { to: "/admin/integrations", label: t(($) => $.nav.integrations), icon: PlugIcon },
        { to: "/admin/sources", label: t(($) => $.nav.sources), icon: CloudIcon },
        { to: "/admin/pipelines", label: t(($) => $.nav.pipelines), icon: WorkflowIcon },
        { to: "/admin/skills", label: t(($) => $.nav.skills), icon: SparklesIcon },
        { to: "/admin/maintenance", label: t(($) => $.nav.maintenance), icon: WrenchIcon },
      ]}
    />
  );
}
