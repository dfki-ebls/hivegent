# hivegent bridge

A small standalone Node/Express service that exposes the hivegent backend through the Vercel **Chat SDK**, so the assistant is reachable from Microsoft Teams (and, with one entry each, any other Chat SDK adapter).

It is **platform-agnostic**: the same handlers serve every adapter. The bridge holds one **service identity**, maps each platform thread to a hivegent conversation, and streams hivegent's reply back into the thread. hivegent stays the single source of truth for messages, documents, retrieval, and tools; the only contract is the HTTP chat call.

## How it works

```
Platform ──webhook──▶ Express ──chat.webhooks[name]──▶ handler
    handler: thread.state → hivegent conversationId
             POST /api/conversations[/{id}]/chat  (Bearer service token)
             parse the UI Message Stream: text-delta → text, tool-input-start → typing status
             thread.post(async generator of text)   # native stream in DMs, buffered in group chats
```

Documents: the bot reads the shared group casebase `@team-kb` via its token's `groups` claim; it is **read-only** (`edit_document`/`write_document`/`save_memory` disabled). Curate the KB via the hivegent web UI.

## Layout

- `src/config.ts` — typed env config
- `src/hivegent/auth.ts` — OIDC client-credentials token provider (cached, single-flight)
- `src/hivegent/client.ts` — request-body builder + start/continue conversation
- `src/hivegent/stream.ts` — SSE → `{text|status|error}` event parser
- `src/adapters.ts` — adapter registry (Teams + Web dev; add platforms here)
- `src/turn.ts` — platform-agnostic turn handler
- `src/bot.ts` / `src/index.ts` — bot wiring + Express bootstrap

## Develop

```bash
npm install
npm test          # vitest (stream parser + request body)
npm run build     # tsc
oxlint --type-aware --type-check
```

Run: the repo dev shell (`nix develop`) exports the bridge's dev config — see the **Bridge** group in `nix/shell.nix` (local backend URL, dev Postgres for state, Web adapter on, Teams/OIDC off). In the shell:

```bash
npm run dev     # tsx watch (or: npm run build && npm start)
```

In production the config comes from the service instead — the NixOS `services.hivegent.bridge` unit or the Docker image (see **Deploy**).

## Local debugging without Teams (Web adapter)

The dev shell already sets `ENABLE_WEB=true` / `ENABLE_TEAMS=false` and omits `OIDC_*`, and the local backend runs auth-disabled (`HIVEGENT_AUTH__ENABLE=0`), so `npm run dev` drives the Web adapter with no Azure or Rauthy. The **same** handlers fire, so you can drive a full turn by POSTing an AI SDK chat request to `POST /api/webhooks/web` (curl or a browser `@chat-adapter/web/react` `useChat` client) — no Azure needed.

## Configuration

Two layered sources, mirroring the backend's `HIVEGENT_CONFIG_FILE` pattern (env wins):

- **JSON file**: non-secret settings, path from `BRIDGE_CONFIG_FILE` with default `config.json`, missing is tolerated.
  Shape: `{ hivegentUrl, oidc: { issuer, clientId }, botUserName, host, port, adapters: { teams, web } }`.
  `host` defaults to `127.0.0.1` and `port` defaults to `3001`.
  `adapters` overrides per-adapter enablement, each also settable via `ENABLE_<NAME>`.
  Unset adapters use their registry default, teams on and web off.
- **Environment variables** — override any file value; the place for secrets (`OIDC_CLIENT_SECRET`, `POSTGRES_URL`, `TEAMS_APP_*`). The dev-shell values live in `nix/shell.nix`; production values come from the deploy target (NixOS `environmentFile` / Docker env).

Auth: the bot needs an OIDC client-credentials token whose `groups` claim carries the shared KB (`team-kb`). Rauthy nests a client's static custom claims under `custom`, and the backend reads both top-level `groups` and `custom.groups` by default — so interactive users and the bot both work with no extra `[claims]` config. Omit all `OIDC_*` to run unauthenticated against an auth-disabled hivegent.

## Teams setup

Register with the Teams CLI (`teams app create --endpoint https://<domain>/api/webhooks/teams`), copy the credentials into `TEAMS_APP_ID`/`TEAMS_APP_PASSWORD`/`TEAMS_APP_TENANT_ID`, and expose `/api/webhooks/*` over HTTPS (Caddy in prod; devtunnel/ngrok locally). Inbound Bot Framework JWTs are validated by the adapter — the bridge forwards headers and does no signature checks.

## Deploy

- **NixOS** — the flake's `nixosModules.default` exposes `services.hivegent.bridge`: set `enable`, `settings` (rendered to a store JSON via `BRIDGE_CONFIG_FILE`), and `environmentFile` (secrets). When both the bridge and the bundled Caddy vhost are enabled, `/api/webhooks/*` is routed to the bridge automatically. The unit shares the systemd hardening baseline (`nix/nixos/hardening.nix`) with the backend.
- **Docker** — the all-in-one image runs the bridge as an optional third dinit service: build with `docker.override { enableBridge = true; }`, mount a `bridge-config.json` at `/data/bridge-config.json`, and set secrets as env. Caddy in the image routes `/api/webhooks/*` to it. See `compose.yaml`.
