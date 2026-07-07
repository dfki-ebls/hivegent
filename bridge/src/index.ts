#!/usr/bin/env node
/** Standalone Express bootstrap: mount every adapter webhook and start the server. */

import { Readable } from "node:stream";

import express, { type Request as ExpressRequest, type Response as ExpressResponse } from "express";

import { createBot } from "./bot.js";
import { loadConfig } from "./config.js";
import { ServiceTokenProvider } from "./hivegent/auth.js";

function toWebRequest(req: ExpressRequest): Request {
  const headers = new Headers();

  for (const [key, value] of Object.entries(req.headers)) {
    if (Array.isArray(value)) {
      headers.set(key, value.join(", "));
    } else if (typeof value === "string") {
      headers.set(key, value);
    }
  }

  const url = `${req.protocol}://${req.get("host") ?? "localhost"}${req.originalUrl}`;
  const body = Buffer.isBuffer(req.body) ? req.body : undefined;

  return new Request(url, { method: req.method, headers, body });
}

function sendWebResponse(res: ExpressResponse, response: Response): void {
  res.status(response.status);
  response.headers.forEach((value, key) => {
    res.setHeader(key, value);
  });

  if (response.body) {
    Readable.fromWeb(response.body).pipe(res);
  } else {
    res.end();
  }
}

const cfg = loadConfig();
const token = cfg.oidc ? new ServiceTokenProvider(cfg.oidc) : undefined;
const chat = await createBot(cfg, { token });

const app = express();

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

for (const [name, handler] of Object.entries(chat.webhooks)) {
  // Raw body for every content-type; the adapter validates the platform JWT.
  app.post(`/api/webhooks/${name}`, express.raw({ type: "*/*" }), async (req, res) => {
    try {
      const response = await handler(toWebRequest(req));
      sendWebResponse(res, response);
    } catch (err) {
      console.error(`webhook ${name} failed`, err);

      if (!res.headersSent) {
        res.status(500).end();
      }
    }
  });
}

await chat.initialize();

app.listen(cfg.port, cfg.host, () => {
  const names = Object.keys(chat.webhooks).join(", ") || "none";
  console.log(`hivegent bridge listening on ${cfg.host}:${cfg.port} (adapters: ${names})`);
});
