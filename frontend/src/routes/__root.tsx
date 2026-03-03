import { TanStackDevtools } from "@tanstack/react-devtools";

import { createRootRoute, Outlet } from "@tanstack/react-router";
import { TanStackRouterDevtoolsPanel } from "@tanstack/react-router-devtools";
import { useEffect } from "react";

import { AppErrorBoundary } from "../components/AppErrorBoundary";
import { Header } from "../components/Header";
import { ThemeProvider } from "../components/ThemeProvider";
import { useSettingsStore } from "../stores/settings-store";

function RootComponent() {
  const initFromBackend = useSettingsStore((state) => state.initFromBackend);

  useEffect(() => {
    void initFromBackend();
  }, [initFromBackend]);

  return (
    <ThemeProvider>
      <AppErrorBoundary>
        <div className="flex h-screen flex-col">
          <Header />
          <main className="flex-1 overflow-hidden">
            <Outlet />
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
      </AppErrorBoundary>
    </ThemeProvider>
  );
}

export const Route = createRootRoute({
  component: RootComponent,
});
