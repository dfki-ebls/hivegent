import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { oidcSpa } from "oidc-spa/vite-plugin";
import { defineConfig } from "vitest/config";
import { thirdPartyLicenses } from "./vite.licenses.ts";

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
    // Next to `index.html`, after the license and additional terms of the app itself.
    thirdPartyLicenses(
      "licenses.txt",
      ["../LICENSE", "../TERMS"]
        .map((file) => readFileSync(new URL(file, import.meta.url), "utf8").trim())
        .join("\n\n"),
    ),
  ],
  build: {
    chunkSizeWarningLimit: 5000,
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
