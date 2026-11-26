import { createFileRoute } from "@tanstack/react-router";
import { ScrollTextIcon, SparklesIcon, UserCogIcon, UserIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { settingsQueries } from "@/api/settings";
import { SettingsShell } from "@/components/settings/settings-shell";
import { keyPrefix } from "@/i18n";
import { enforceLogin } from "@/oidc";

const NAV_T = keyPrefix(($) => $.settings.nav);

/** Every user's own settings, one page each. */
export const Route = createFileRoute("/settings")({
  beforeLoad: enforceLogin,
  // The workspaces every library section lists arrive with the server settings.
  loader: ({ context }) => context.queryClient.query(settingsQueries.server()),
  component: SettingsLayout,
});

function SettingsLayout() {
  const { t } = useTranslation(undefined, NAV_T);

  return (
    <SettingsShell
      title={t(($) => $.title)}
      pages={[
        { to: "/settings/profile", label: t(($) => $.profile), icon: UserIcon },
        { to: "/settings/instructions", label: t(($) => $.instructions), icon: ScrollTextIcon },
        { to: "/settings/skills", label: t(($) => $.skills), icon: SparklesIcon },
        { to: "/settings/account", label: t(($) => $.account), icon: UserCogIcon },
      ]}
    />
  );
}
