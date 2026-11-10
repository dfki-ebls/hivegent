#!/usr/bin/env node
/** Standalone srvx bootstrap: route every adapter webhook and start the server. */

import { serve, type ServerRequest } from "srvx";
import { BodyTooLargeError } from "srvx/body-limit";

import { createBot } from "./bot.js";
import { loadConfig } from "./config.js";
import { ServiceTokenProvider } from "./hivegent/auth.js";

// Adapters buffer the body before verifying the platform JWT, so cap what an anonymous sender can make us hold.
const MAX_BODY_BYTES = 1024 * 1024;

const cfg = loadConfig();
const token = cfg.oidc ? new ServiceTokenProvider(cfg.oidc) : undefined;
const chat = await createBot(cfg, { token });

// Exact `METHOD /path` keys, so unknown routes and methods fall through to 404.
// Turns run in the background after the webhook answers, `waitUntil` lets shutdown await them.
const routes = new Map<string, (request: ServerRequest) => Response | Promise<Response>>([
  ["GET /health", () => Response.json({ status: "ok" })],
  ...Object.entries(chat.webhooks).map(
    ([name, handler]) =>
      [
        `POST /api/webhooks/${name}`,
        (request: ServerRequest) => handler(request, { waitUntil: request.waitUntil }),
      ] as const,
  ),
]);

await chat.initialize();

const server = serve({
  hostname: cfg.host,
  port: cfg.port,
  silent: true,
  maxRequestBodySize: MAX_BODY_BYTES,
  gracefulShutdown: false,
  fetch: (request) =>
    routes.get(`${request.method} ${new URL(request.url).pathname}`)?.(request) ??
    new Response(null, { status: 404 }),
  // Without this handler, a rejected webhook would be an unhandled rejection.
  error: (err) => {
    if (err instanceof BodyTooLargeError) {
      return new Response(null, { status: 413 });
    }

    console.error("request failed", err);
    return new Response(null, { status: 500 });
  },
});

// Drain requests and in-flight turns, then close the Postgres pool that would keep the process alive.
async function shutdown(): Promise<void> {
  await server.close();
  await chat.shutdown();
  process.exit();
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);

await server.ready();
const names = Object.keys(chat.webhooks).join(", ") || "none";
console.log(`hivegent bridge listening on ${cfg.host}:${cfg.port} (adapters: ${names})`);
