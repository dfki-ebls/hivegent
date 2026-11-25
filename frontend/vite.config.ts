import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { oidcSpa } from "oidc-spa/vite-plugin";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";
import csp from "./csp.json" with { type: "json" };
import { thirdPartyLicenses } from "./vite.licenses.ts";

/**
 * The Content-Security-Policy of `csp.json` as a meta tag, the floor for any server of the build.
 * The vhost sends it as a header too, together with what a meta tag cannot carry
 * (`frame-ancestors`) or only the deployment knows (the identity provider's origin).
 * The dev server injects inline scripts, so only the build carries it.
 */
const contentSecurityPolicy: Plugin = {
  name: "content-security-policy",
  apply: "build",
  transformIndexHtml: () => [
    {
      tag: "meta",
      attrs: {
        "http-equiv": "Content-Security-Policy",
        content: Object.entries(csp)
          .map(([directive, sources]) => [directive, ...sources].join(" "))
          .join("; "),
      },
      injectTo: "head-prepend",
    },
  ],
};

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
    }),
    // Scripts see placeholders instead of tokens, which only same-origin requests swap back,
    // and the APIs that swap them are frozen against monkey patching.
    oidcSpa({
      tokenSubstitution: { enabled: true },
      browserRuntimeFreeze: { enabled: true },
    }),
    viteReact({ compiler: true }),
    tailwindcss(),
    contentSecurityPolicy,
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
  // Module workers, so a worker loads its libraries on demand.
  worker: {
    format: "es",
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
