import { WrenchIcon } from "lucide-react";

/**
 * Full-screen notice shown to non-admins while the backend is in
 * maintenance mode. The settings store keeps polling the backend in
 * the background, so the app loads by itself once an admin turns the
 * mode back off.
 */
export function MaintenanceScreen() {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 p-8 text-center">
      <WrenchIcon className="h-12 w-12 text-muted-foreground" />
      <h1 className="text-2xl font-semibold">Down for maintenance</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        The application is temporarily unavailable while an administrator performs maintenance.
        This page refreshes automatically once the app is back.
      </p>
    </div>
  );
}
