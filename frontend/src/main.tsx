import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import ReactDOM from "react-dom/client";

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

const rootElement = document.getElementById("app");
if (rootElement && !rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <StrictMode>
      <OidcInitializationGate>
        <RouterProvider router={router} />
      </OidcInitializationGate>
    </StrictMode>,
  );
}
