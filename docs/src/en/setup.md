# Setup

Hivegent ships as one container image, `ghcr.io/dfki-ebls/hivegent`.
A typical deployment runs three containers with Docker Compose: Hivegent, PostgreSQL, and an OIDC identity provider.
The repository's `compose.yaml` is a ready-to-run example with Rauthy.

> **Additional terms of use:** Hivegent is provided under the MIT license and the DFKI's [additional terms of use](terms.md).
> By downloading, installing, or using the software, you accept the additional terms.
> Please read them in full beforehand, the German version is authoritative.

## Prerequisites

- Docker with Compose, or Podman.
- An OpenAI-compatible model endpoint, hosted (such as OpenAI) or local (such as vLLM, SGLang, or Ollama).
- Optional: an NVIDIA GPU for faster conversion and OCR.

## Quick start

1. Copy `compose.yaml` into an empty directory.
2. Read the [additional terms of use](terms.md) and accept them by setting `HIVEGENT_ACCEPT_TERMS` to `true`, Hivegent does not start otherwise.
3. Set the language model and the OIDC issuer as described below.
4. Run `docker compose up -d`, the first start takes a few minutes.
5. Open <http://localhost:8080>.

## Configuration

The backend reads `/data/config.toml` and `HIVEGENT_*` environment variables, which win.
Nested keys use a double underscore, so `[llm] model` becomes `HIVEGENT_LLM__MODEL`.
Mount secrets such as API keys as files named like their variable under `/run/secrets`, for example with Compose `secrets`.

```toml
[db]
url = "postgresql+psycopg://hivegent:hivegent@postgresql:5432/hivegent"

[llm]
model = "your-chat-model"
aux_model = "your-small-vision-model"
base_url = "http://your-llm-host:8000/v1"
inference_provider = "vllm"
# api_key = "..."   # only if your provider requires one

[auth]
issuer = "http://auth.localhost:8081/auth/v1"
audience = ["hivegent-*"]
```

- `llm.model`: the chat model, which needs a large context and tool calling.
- `llm.aux_model`: a small, fast, vision-capable model for conversion, captions, and titles.
  Defaults to the chat model.
- `llm.base_url` and `llm.api_key`: your endpoint.
- `llm.inference_provider`: `llama.cpp`, `vllm`, or `openai` (default).
  Match your server to enable reasoning control and chat-template fixes.
- `db.url`: the PostgreSQL connection string.
  The bundled database has pgvector enabled.
- `auth.issuer`: the OIDC issuer URL.
- `auth.audience`: accepted token audiences.
  `hivegent-*` accepts every Hivegent client.

Common extras are `HIVEGENT_TOOLS__SEARCH_URL` for web search, `HIVEGENT_SECURITY__WEB_URLS__ALLOW_HOSTS='["*"]'` for web fetch, `HIVEGENT_EMBEDDING__MODEL` for the embedding model, and `HIVEGENT_MCP__ENABLE=true` for the MCP endpoint.

`tools.disabled` withholds tools from the model by the names `GET /api/debug/tools` lists.
It defaults to `["list_conversations", "get_conversation"]`, since sub-agents already reach past conversations.
An unknown name stops the server at startup.

Conversation files (`/tmp` and staged changes) are deleted after `conversations.ttl_hours` of inactivity (default 72), set with `HIVEGENT_CONVERSATIONS__TTL_HOURS`.

User-provided LLM and MCP URLs stay disabled until their hosts are listed under `[security.user_urls] allow_hosts`.
An entry permits the domain and its subdomains, and `"*"` permits any public host.
These requests and all model-controlled web requests pass through the bundled Smokescreen proxy, which rejects private and reserved addresses.
Operator-configured URLs such as `llm.base_url` connect directly.

Web search needs a metasearch service, which Hivegent does not ship, the same way it leaves PostgreSQL to you.
SearXNG is the default backend: `compose.yaml` runs its upstream image, and the NixOS option `services.hivegent.searxng.createLocally` wires in the host's `services.searx`.
OpenSERP works as well with `tools.search_backend = "openserp"`, though it searches the general web only and reports no publication dates.
Point `tools.search_url` at the service, otherwise `web_search` stays hidden, and for SearXNG list `json` under its `search.formats`.
`web_fetch` is enabled separately by `[security.web_urls] allow_hosts`, where `"*"` permits any public page the user or a search named and a list of domains confines it.
A URL the model writes itself is refused, so instructions planted on a page cannot carry conversation data out in a link.

To reach an operator endpoint over a unix socket, map its host name under `[network.unix_sockets]`, for example `HIVEGENT_NETWORK__UNIX_SOCKETS='{"llmhop": "/run/llmhop/default.sock"}'`.
`llm.base_url = "http://llmhop/v1"` then connects to the socket.
User-provided URLs never use this mapping.
The NixOS module runs as a dynamic user, so give the socket its own static group (not `hivegent`) with mode `0660` and add it to `systemd.services.hivegent.serviceConfig.SupplementaryGroups`.

## Identity provider (OIDC)

Hivegent works with any OIDC provider, such as Rauthy, Keycloak, Authentik, or Auth0.
The browser reads the issuer and client id from the backend at runtime, so no rebuild is needed.

For the bundled Rauthy:

1. Read the one-time admin password with `docker compose logs rauthy`.
2. Log in at <http://auth.localhost:8081>.
3. Create a public client `hivegent-spa` with PKCE (S256), and set the redirect URI and allowed origin to `http://localhost:8080`.
4. Create users and, optionally, groups.

### Groups and roles

Access is driven by the token claims, which your provider must include in the access token.

- Every user has a private workspace.
- Groups come from the `groups` or `custom.groups` claim (`claims.groups`).
  An entry like `engineering` grants its shared workspace, and `engineering:read` or `engineering:write` sets the permission.
- The `admin` role from the `roles` or `custom.roles` claim grants administrator actions such as maintenance mode and data resets.

### Group identity and renaming

A groups claim entry is either an object or a bare string.

An RFC 9068 object `{"value": "<id>", "display": "<name>"}` separates the id from the display name.
The id appears in paths (`@<id>/notes.md`), so renaming the group keeps its workspace.
Entra ID, and Keycloak, Authentik, Okta, or Auth0 with a claim mapper on the group id, work this way.

A bare string, which is all Rauthy can emit, is both name and id.
Renaming such a group leaves its documents behind under the old name.

## Updating

```bash
docker compose pull
docker compose up -d
```

Database migrations run automatically on startup.

## Production notes

- Replace the example secrets, keys, and passwords.
- Serve the stack over HTTPS, through the bundled proxy with a real domain or your own reverse proxy.
- For GPU processing, uncomment the NVIDIA runtime section in `compose.yaml`.
