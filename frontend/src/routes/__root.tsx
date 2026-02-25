import { TanStackDevtools } from "@tanstack/react-devtools";

import { createRootRoute, Outlet, useLocation } from "@tanstack/react-router";
import { TanStackRouterDevtoolsPanel } from "@tanstack/react-router-devtools";
import { useEffect } from "react";

import { AppErrorBoundary } from "../components/AppErrorBoundary";
import { AuthGate } from "../components/AuthGate";
import { AuthProvider } from "../components/AuthProvider";
import { Header } from "../components/Header";
import { ThemeProvider } from "../components/ThemeProvider";
import { useSettingsStore } from "../stores/settings-store";

// Routes that don't require authentication
const PUBLIC_ROUTES = ["/", "/auth/callback"];

function RootComponent() {
  const location = useLocation();
  const isPublicRoute = PUBLIC_ROUTES.includes(location.pathname);
  const initFromBackend = useSettingsStore((state) => state.initFromBackend);

  useEffect(() => {
    void initFromBackend();
  }, [initFromBackend]);

  return (
    <ThemeProvider>
      <AppErrorBoundary>
        <AuthProvider>
          <div className="flex h-screen flex-col">
            <Header />
            <main className="flex-1 overflow-hidden">
              {isPublicRoute ? (
                <Outlet />
              ) : (
                <AuthGate>
                  <Outlet />
                </AuthGate>
              )}
            </main>
            <TanStackDevtools
              config={{
                position: "bottom-left",
              }}
              plugins={[
                {
                  name: "Tanstack Router",
                  render: <TanStackRouterDevtoolsPanel />,
                },
              ]}
            />
          </div>
        </AuthProvider>
      </AppErrorBoundary>
    </ThemeProvider>
  );
}

export const Route = createRootRoute({
  component: RootComponent,
});
