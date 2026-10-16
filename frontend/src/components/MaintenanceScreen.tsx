import { WrenchIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/EmptyState";

/**
 * Full-screen notice shown to non-admins while the backend is in
 * maintenance mode. The settings store keeps polling the backend in
 * the background, so the app loads by itself once an admin turns the
 * mode back off.
 */
export function MaintenanceScreen() {
  const { t } = useTranslation();

  return (
    <EmptyState
      icon={<WrenchIcon />}
      title={t(($) => $.app.maintenance.title)}
      description={t(($) => $.app.maintenance.description)}
      className="h-dvh"
    />
  );
}
