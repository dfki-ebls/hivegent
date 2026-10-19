import { readFileSync } from "node:fs";
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
    {
      name: "hivegent-notices",
      generateBundle: {
        order: "post",
        handler(_, bundle) {
          const notices = bundle["licenses.txt"];

          if (notices?.type !== "asset") {
            throw new Error("Missing generated license notices");
          }

          notices.source = [
            readFileSync(new URL("../LICENSE", import.meta.url), "utf8"),
            readFileSync(new URL("../TERMS", import.meta.url), "utf8"),
            notices.source,
          ].join("\n\n");
        },
      },
    },
  ],
  build: {
    chunkSizeWarningLimit: 5000,
    // Served next to `index.html`, the notices that the bundled packages require,
    // prefixed with the license and additional terms by the plugin above.
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
