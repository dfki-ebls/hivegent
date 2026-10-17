# hivegent bridge

A small Node and Express service that exposes the hivegent backend through the Vercel Chat SDK, for Microsoft Teams and any other Chat SDK adapter.

The same handlers serve every adapter.
The bridge holds one service identity, maps each platform thread to a hivegent conversation, and streams the reply back.
Its only contract with hivegent is the HTTP chat call.

## How it works

```
Platform ──webhook──▶ Express ──chat.webhooks[name]──▶ handler
    handler: thread.state → hivegent conversationId
             POST /api/conversations[/{id}]/chat  (Bearer service token)
             parse the UI Message Stream: text-delta → text, tool-input-start → typing status
             thread.post(async generator of text)
```

The bot reads the shared group workspace `@team-kb` through a `team-kb:read` groups claim.
Every turn runs in read mode, so no workspace is writable whatever the claim grants, and `edit_document`, `write_document`, and `save_memory` are disabled on top, so curate the workspace in the web UI.

## Layout

- `src/config.ts`: typed config
- `src/hivegent/auth.ts`: OIDC client-credentials token provider
- `src/hivegent/client.ts`: request body and conversation calls
- `src/hivegent/stream.ts`: stream event parser
- `src/adapters.ts`: adapter registry (Teams and Web)
- `src/turn.ts`: platform-agnostic turn handler
- `src/bot.ts`, `src/index.ts`: bot wiring and Express bootstrap

## Develop

```bash
npm install
npm test
npm run build
oxlint --type-aware --type-check
npm run dev
```

The repo dev shell (`nix develop`) exports the dev config, see the Bridge group in `nix/shell.nix`.
It enables the Web adapter, disables Teams and OIDC, and the local backend runs with `HIVEGENT_AUTH__ENABLE=0`.
To drive a full turn without Teams, POST an AI SDK chat request to `/api/webhooks/web`.

## Configuration

Environment variables override a JSON file read from `BRIDGE_CONFIG_FILE` (default `config.json`, missing is fine).

- File shape: `{ hivegentUrl, oidc: { issuer, clientId }, reasoningEffort, botUserName, host, port, adapters: { teams, web } }`.
- `host` defaults to `127.0.0.1`, `port` to `3001`, `reasoningEffort` to `high`.
- `ENABLE_<NAME>` overrides an adapter, which otherwise uses its default (Teams on, Web off).
- Secrets (`OIDC_CLIENT_SECRET`, `POSTGRES_URL`, `TEAMS_APP_*`) go in credential files named like the variable in `$CREDENTIALS_DIRECTORY`.
  A set variable wins over its file.

The bot needs an OIDC client-credentials token whose groups claim carries `team-kb`.
The backend reads both `groups` and `custom.groups`, so Rauthy's static custom claims work without extra config.
Omit all `OIDC_*` to run against an auth-disabled hivegent.

## Teams setup

Register with `teams app create --endpoint https://<domain>/api/webhooks/teams`.
Put the credentials into `TEAMS_APP_ID`, `TEAMS_APP_PASSWORD`, and `TEAMS_APP_TENANT_ID`.
Expose `/api/webhooks/*` over HTTPS.
The adapter validates inbound Bot Framework tokens.

## Deploy

- NixOS: `services.hivegent.bridge` from the flake's `nixosModules.default` takes `enable`, `settings`, and `credentials`.
  With the bundled Caddy vhost, `/api/webhooks/*` routes to the bridge automatically.
- Docker: build with `docker.override { enableBridge = true; }`, mount `bridge-config.json` at `/data/bridge-config.json` and secrets under `/run/secrets`.
  See `compose.yaml`.
