import { TanStackDevtools } from "@tanstack/react-devtools";
import { useQuery } from "@tanstack/react-query";
import { ReactQueryDevtoolsPanel } from "@tanstack/react-query-devtools";
import { createRootRoute, Outlet } from "@tanstack/react-router";
import { TanStackRouterDevtoolsPanel } from "@tanstack/react-router-devtools";

import { AppErrorBoundary } from "@/components/AppErrorBoundary";
import { Header } from "@/components/Header";
import { ImpersonationBanner } from "@/components/ImpersonationBanner";
import { MaintenanceScreen } from "@/components/MaintenanceScreen";
import { NotFound } from "@/components/NotFound";
import { Toaster } from "@/components/ui/toast";
import { MaintenanceError } from "@/lib/api";
import { useOidc } from "@/oidc";
import { settingsQueries } from "@/queries/settings";

function RootComponent() {
  const { isUserLoggedIn } = useOidc();
  // The one observer that loads the server settings. A maintenance rejection
  // shows the maintenance screen while the query keeps retrying.
  const { failureReason } = useQuery({ ...settingsQueries.server(), enabled: isUserLoggedIn });

  if (failureReason instanceof MaintenanceError) {
    return <MaintenanceScreen />;
  }

  return (
    <AppErrorBoundary>
      <Toaster />
      <div className="flex h-dvh flex-col">
        <ImpersonationBanner />
        <Header />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
        <TanStackDevtools
          config={{ position: "bottom-left" }}
          plugins={[
            {
              name: "Tanstack Router",
              render: <TanStackRouterDevtoolsPanel />,
            },
            {
              name: "Tanstack Query",
              render: <ReactQueryDevtoolsPanel />,
            },
          ]}
        />
      </div>
    </AppErrorBoundary>
  );
}

export const Route = createRootRoute({
  component: RootComponent,
  notFoundComponent: NotFound,
});
