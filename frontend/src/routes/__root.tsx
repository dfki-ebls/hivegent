import { type QueryClient, useQuery } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";

import { Header } from "@/components/header";
import { ImpersonationBanner } from "@/components/impersonation-banner";
import { MaintenanceScreen } from "@/components/maintenance-screen";
import { Toaster } from "@/components/ui/toast";
import { MaintenanceError } from "@/api/client";
import { settingsQueries } from "@/api/settings";
import { useOidc } from "@/oidc";

function RootComponent() {
  const { isUserLoggedIn } = useOidc();
  // The one observer that loads the server settings. A maintenance rejection
  // shows the maintenance screen while the query keeps retrying.
  const { failureReason } = useQuery({ ...settingsQueries.server(), enabled: isUserLoggedIn });

  if (failureReason instanceof MaintenanceError) {
    return <MaintenanceScreen />;
  }

  return (
    <>
      <Toaster />
      <div className="flex h-dvh flex-col">
        <ImpersonationBanner />
        <Header />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </>
  );
}

/** What every route's `beforeLoad` and `loader` receive. */
interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootComponent,
});
