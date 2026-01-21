import { Outlet, createRootRoute } from '@tanstack/react-router';
import { TanStackRouterDevtoolsPanel } from '@tanstack/react-router-devtools';
import { TanStackDevtools } from '@tanstack/react-devtools';

import { Header } from '../components/Header';
import { ThemeProvider } from '../components/ThemeProvider';

export const Route = createRootRoute({
  component: () => (
    <ThemeProvider>
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
    </ThemeProvider>
  ),
});
