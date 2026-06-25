import { WrenchIcon } from "lucide-react";

import { FullScreenNotice } from "./FullScreenNotice";

/**
 * Full-screen notice shown to non-admins while the backend is in
 * maintenance mode. The settings store keeps polling the backend in
 * the background, so the app loads by itself once an admin turns the
 * mode back off.
 */
export function MaintenanceScreen() {
  return (
    <FullScreenNotice
      icon={<WrenchIcon className="h-12 w-12 text-muted-foreground" />}
      title="Down for maintenance"
    >
      The application is temporarily unavailable while an administrator performs maintenance. This
      page refreshes automatically once the app is back.
    </FullScreenNotice>
  );
}
