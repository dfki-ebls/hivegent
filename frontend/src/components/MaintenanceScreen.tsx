import { WrenchIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { FullScreenNotice } from "@/components/FullScreenNotice";

/**
 * Full-screen notice shown to non-admins while the backend is in
 * maintenance mode. The settings store keeps polling the backend in
 * the background, so the app loads by itself once an admin turns the
 * mode back off.
 */
export function MaintenanceScreen() {
  const { t } = useTranslation();

  return (
    <FullScreenNotice
      icon={<WrenchIcon className="h-12 w-12 text-muted-foreground" />}
      title={t(($) => $.app.maintenance.title)}
    >
      {t(($) => $.app.maintenance.description)}
    </FullScreenNotice>
  );
}
