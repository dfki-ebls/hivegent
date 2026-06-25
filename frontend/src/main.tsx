import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import ReactDOM from "react-dom/client";

import { BootstrapGate } from "./components/BootstrapGate";
import { ThemeProvider } from "./components/ThemeProvider";
import { OidcInitializationGate } from "./oidc";
import { routeTree } from "./routeTree.gen";

import "./styles.css";

// A redeploy re-hashes lazily imported chunks, so a tab opened beforehand fails
// to fetch them; reloading pulls the fresh (no-store) index.html with current
// chunk names. The timestamp guard stops a genuinely missing chunk from looping.
window.addEventListener("vite:preloadError", () => {
  const key = "vite:preload-reload-at";
  if (Date.now() - Number(sessionStorage.getItem(key)) < 10_000) return;
  sessionStorage.setItem(key, String(Date.now()));
  window.location.reload();
});

const router = createRouter({
  routeTree,
  context: {},
  defaultPreload: "intent",
  scrollRestoration: true,
  defaultStructuralSharing: true,
  defaultPreloadStaleTime: 0,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

// Render immediately and let BootstrapGate drive startup from inside React: it
// waits for the backend, fetches the runtime config, and bootstraps OIDC while
// showing the connection spinner, then mounts the initialization gate. The SPA
// no longer blocks rendering on /api/config, so a booting backend shows the
// spinner instead of a blank page. ThemeProvider sits on top so even the
// connection screen honors dark mode.
const rootElement = document.getElementById("app");
if (rootElement && !rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <StrictMode>
      <ThemeProvider>
        <BootstrapGate>
          <OidcInitializationGate>
            <RouterProvider router={router} />
          </OidcInitializationGate>
        </BootstrapGate>
      </ThemeProvider>
    </StrictMode>,
  );
}
