import { Outlet, createRootRoute, useLocation } from '@tanstack/react-router';
import { TanStackRouterDevtoolsPanel } from '@tanstack/react-router-devtools';
import { TanStackDevtools } from '@tanstack/react-devtools';

import { AuthGate } from '../components/AuthGate';
import { AuthProvider } from '../components/AuthProvider';
import { Header } from '../components/Header';
import { ThemeProvider } from '../components/ThemeProvider';

// Routes that don't require authentication
const PUBLIC_ROUTES = ['/', '/auth/callback'];

function RootComponent() {
  const location = useLocation();
  const isPublicRoute = PUBLIC_ROUTES.includes(location.pathname);

  return (
    <ThemeProvider>
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
              position: 'bottom-left',
            }}
            plugins={[
              {
                name: 'Tanstack Router',
                render: <TanStackRouterDevtoolsPanel />,
              },
            ]}
          />
        </div>
      </AuthProvider>
    </ThemeProvider>
  );
}

export const Route = createRootRoute({
  component: RootComponent,
});
