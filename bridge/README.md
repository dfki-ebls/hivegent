# hivegent bridge

A small Node service on srvx that exposes the hivegent backend through the Vercel Chat SDK, for Microsoft Teams and any other Chat SDK adapter.

The same handlers serve every adapter.
The bridge holds one service identity, maps each platform thread to a hivegent conversation, and streams the reply back.
Its only contract with hivegent is the HTTP chat call.

## How it works

```
Platform ──webhook──▶ srvx ──chat.webhooks[name]──▶ handler
    handler: thread.state → hivegent conversationId (a fresh UUID for a new thread)
             POST /api/v1/conversations/{id}/chat  (Bearer service token)
             parse the UI Message Stream: text-delta → text, tool-input-start → typing status
             thread.post(async generator of text)
```

## What the bot may do

The backend names the bot's OIDC client in `auth.service_client_id` (`hivegent-bot`), and every token of that client acts as a service principal whose capabilities follow from its kind, not from its claims or its requests.

- It sees the global workspace (`/srv`) alone and writes nothing, whatever groups or roles its token carries.
- Every turn runs in read mode, whatever the request says.
- It is offered no memory and no conversation tools, and the chat route is the only route it reaches, so uploads, document writes, and conversation listings are refused with 403.

Curate the global workspace in the web UI as an administrator.
All Teams users share the bot's one identity, so every Teams conversation is stored under it, which is why nothing that lists or searches conversations is reachable for it.
Its requests and chat turns spend the `rate_limits.teams` buckets, which all Teams users share.

The bridge fails closed on both counts.
Its token request and this check retry a network error, timeout, 5xx, or 429 with a backoff from 1s to 30s for up to five minutes, so the bridge can boot beside an IdP or backend still starting, and exit at once on any other failure.
The Teams adapter refuses to start without `oidc`, and the bot answers only messages whose tenant (`conversation.tenantId`, else `channelData.tenant.id`) is in `teams.allowedTenants`, dropping every other one without a reply. The handlers check it first, which the adapter reaches only after it validated the Bot Framework JWT, so the tenant comes from an activity Microsoft vouches for.

## Layout

- `src/config.ts`: typed config
- `src/hivegent/auth.ts`: OIDC client-credentials token provider
- `src/hivegent/client.ts`: request body and conversation calls
- `src/hivegent/stream.ts`: stream event parser
- `src/adapters.ts`: adapter registry (Teams and Web)
- `src/turn.ts`: platform-agnostic turn handler
- `src/bot.ts`, `src/index.ts`: bot wiring and srvx bootstrap

## Develop

```bash
npm install
npm test
npm run build
oxlint --type-aware --type-check
npm run dev
```

The repo dev shell (`nix develop`) points `BRIDGE_CONFIG_FILE` at a dev config, see the Bridge group in `nix/shell.nix`.
It enables the Web adapter, disables Teams, omits `oidc`, and the local backend runs as `hivegent dev`, whose development user every request acts as.
To drive a full turn without Teams, POST an AI SDK chat request to `/api/webhooks/web`.

## Configuration

One JSON file, read from `BRIDGE_CONFIG_FILE` (default `config.json`), holds every non-secret setting and is validated at startup, rejecting unknown keys.

```json
{
  "backendUrl": "http://127.0.0.1:8000",
  "host": "127.0.0.1",
  "port": 3003,
  "botUserName": "hivegent",
  "oidc": { "issuer": "https://auth.example.com", "clientId": "hivegent-bot", "scope": "openid" },
  "adapters": { "teams": true, "web": false },
  "teams": { "appId": "<app id>", "tenantId": "<tenant id>", "allowedTenants": ["<tenant id>"] }
}
```

- `backendUrl` and `adapters.teams` are required. `backendUrl` has no trailing slash, while `oidc.issuer` is the issuer exactly as the IdP names it in `iss`, trailing slash included, and both are `https` unless they name a loopback host.
- `host` defaults to `127.0.0.1`, `port` to `3003`, `botUserName` to `hivegent`, and `adapters.web` to `false`.
- The Teams adapter needs both `oidc` and `teams`, whose `allowedTenants` names the Microsoft Entra tenant ids it answers.
- Omit `oidc` to run the Web adapter against `hivegent dev`, where the bot acts as the local dev user instead. The Web adapter answers anyone, so the configuration refuses it together with `oidc`, which leaves it no credentials to reach a real backend with.

Secrets never go in the file.
The bridge reads each by name from its environment variable, else from the credential file `hivegent-bridge.<NAME>` in `$CREDENTIALS_DIRECTORY`.

- `POSTGRES_URL`, always.
- `OIDC_CLIENT_SECRET`, with `oidc`.
- `TEAMS_APP_PASSWORD`, with `teams`.

The bot needs an OIDC client-credentials token of the client the backend names in `auth.service_client_id` and accepts in `auth.audience`.
It needs no groups or roles claim.

## Teams setup

Register with `teams app create --endpoint https://<domain>/api/webhooks/teams`.
Put the app id and tenant id into `teams.appId` and `teams.tenantId`, the password into the `TEAMS_APP_PASSWORD` secret, and the tenants whose users may chat into `teams.allowedTenants`, usually that same tenant id.
Expose `/api/webhooks/*` over HTTPS.
The adapter validates inbound Bot Framework tokens.

## Deploy

- NixOS: `services.hivegent.bridge` from the flake's `nixosModules.default` takes `enable`, `settings`, and `credentials`.
  With the bundled Caddy vhost, `/api/webhooks/*` routes to the bridge automatically.
- Docker: build with `docker.override { enableBridge = true; }`, mount `bridge-config.json` at `/data/bridge-config.json` and secrets under `/run/secrets`.
  See `compose.yaml`.
