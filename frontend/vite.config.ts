import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { oidcSpa } from "oidc-spa/vite-plugin";
import { defineConfig } from "vitest/config";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
    }),
    oidcSpa(),
    viteReact(),
    tailwindcss(),
  ],
  build: {
    chunkSizeWarningLimit: 5000,
    // Served next to `index.html`, the notices that the bundled packages require.
    license: { fileName: "licenses.txt" },
  },
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/mcp": { target: "http://localhost:8000", changeOrigin: true, ws: true },
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
