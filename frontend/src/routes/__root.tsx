import { TanStackDevtools } from "@tanstack/react-devtools";
import { createRootRoute, Outlet } from "@tanstack/react-router";
import { TanStackRouterDevtoolsPanel } from "@tanstack/react-router-devtools";
import { useEffect } from "react";

import { AppErrorBoundary } from "../components/AppErrorBoundary";
import { Header } from "../components/Header";
import { ImpersonationBanner } from "../components/ImpersonationBanner";
import { MaintenanceScreen } from "../components/MaintenanceScreen";
import { ThemeProvider } from "../components/ThemeProvider";
import { Toaster } from "../components/ui/sonner";
import { useOidc } from "../oidc";
import { useSettingsStore } from "../stores/settings-store";

function RootComponent() {
  const { isUserLoggedIn } = useOidc();
  const initFromBackend = useSettingsStore((state) => state.initFromBackend);
  const maintenance = useSettingsStore((state) => state.maintenance);

  useEffect(() => {
    if (isUserLoggedIn) {
      void initFromBackend();
    }
  }, [isUserLoggedIn, initFromBackend]);

  if (maintenance) {
    return (
      <ThemeProvider>
        <MaintenanceScreen />
      </ThemeProvider>
    );
  }

  return (
    <ThemeProvider>
      <AppErrorBoundary>
        <Toaster />
        <div className="flex h-screen flex-col">
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
            ]}
          />
        </div>
      </AppErrorBoundary>
    </ThemeProvider>
  );
}

export const Route = createRootRoute({
  component: RootComponent,
});
