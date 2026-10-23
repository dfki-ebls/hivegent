import { createFileRoute, Outlet } from "@tanstack/react-router";
import { LockIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { useIsAdmin } from "@/queries/settings";
import { EmptyState } from "@/components/EmptyState";
import { enforceLogin } from "@/oidc";

const T_OPTIONS = keyPrefix(($) => $.app.adminOnly);

/** The debugging consoles, for admins only, which everyone else sees locked. */
export const Route = createFileRoute("/debug")({
  beforeLoad: enforceLogin,
  component: DebugLayout,
});

function DebugLayout() {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const isAdmin = useIsAdmin();

  if (!isAdmin) {
    return (
      <EmptyState
        icon={<LockIcon />}
        title={t(($) => $.title)}
        description={t(($) => $.description)}
        className="h-full"
      />
    );
  }

  return <Outlet />;
}
