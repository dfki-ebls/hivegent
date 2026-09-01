# Setup

Hivegent ships as a single container image published at `ghcr.io/dfki-ebls/hivegent`.
A typical deployment runs three containers with Docker Compose: the Hivegent app, a PostgreSQL database, and an OIDC identity provider.
The repository's `compose.yaml` is a complete, ready to run example that uses Rauthy as the provider.

## Prerequisites

- Docker with the Compose plugin (Podman works too).
- An OpenAI-compatible language model endpoint, either a hosted API such as OpenAI or a local server such as vLLM, SGLang, or Ollama.
- Optional: an NVIDIA GPU for faster document conversion and OCR.

## Quick start

1. Copy `compose.yaml` from the repository into an empty directory.
2. Adjust the configuration as described below, in particular the language model and the OIDC issuer.
3. Start the stack:

```bash
docker compose up -d
```

4. The first start pulls the images and initializes the database, which can take a few minutes.
5. Open <http://localhost:8080> in your browser.

## Configuration

The backend reads a TOML config file (mounted at `/data/config.toml`) and also accepts `HIVEGENT_*` environment variables that override individual keys.
Nested keys use a double underscore, so `[llm] model` becomes `HIVEGENT_LLM__MODEL`.

A minimal config looks like this:

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

The most important settings:

- `llm.model`: the main chat model, which needs a large context window and tool calling.
- `llm.aux_model`: a small, fast, vision-capable model used for document conversion, captions, and titles. It falls back to the main model when unset.
- `llm.base_url` and `llm.api_key`: your OpenAI-compatible endpoint.
- `llm.inference_provider`: the endpoint implementation, one of `llama.cpp`, `vllm`, or `openai`. It defaults to `openai`, which sends only standard fields. Set it to match your server, otherwise reasoning control and the self-hosted chat-template fixes stay off.
- `db.url`: the PostgreSQL connection string. The bundled database already has pgvector enabled.
- `auth.issuer`: the OIDC provider's issuer URL.
- `auth.audience`: the token audiences to accept. The entry `hivegent-*` accepts every current and future Hivegent client.

Common extras are `HIVEGENT_TOOLS__ENABLE_WEB=true` to allow web search, `HIVEGENT_EMBEDDING__MODEL` to change the embedding model, and `HIVEGENT_MCP__ENABLE=true` to expose the MCP endpoint.

`tools.disabled` withholds individual tools from the model, by the names the admin tool console lists (`GET /api/debug/tools`).
It defaults to `["jq", "list_conversations", "get_conversation"]`, which suits a deployment whose documents are documents rather than JSON.
Every tool not named here reaches the model on every request, so the list is where you trade a capability for the context its schema costs.
Naming a tool that does not exist stops the server at startup rather than silently excluding nothing.

User-provided LLM and MCP server URLs are disabled until their hosts are listed under `[security.user_urls] allow_hosts`.
An entry permits that domain and its subdomains, an empty list denies every host, and `"*"` permits any public host.
These requests and all model-controlled web requests pass through the bundled Smokescreen proxy, which rejects private and reserved destinations after DNS resolution.
Operator-configured URLs such as `llm.base_url` remain direct and trusted.

## Identity provider (OIDC)

Hivegent delegates login to an OIDC provider and is provider-agnostic.
The example uses Rauthy, but Keycloak, Authentik, Auth0, and others work the same way.
The browser reads the issuer and client id from the backend at runtime, so switching providers needs no rebuild.

For the bundled Rauthy example:

1. After the first start, read the one-time admin password from the logs with `docker compose logs rauthy`.
2. Log in to the Rauthy admin interface at <http://auth.localhost:8081>.
3. Create a public client with the id `hivegent-spa` using PKCE with S256, and set both the redirect URI and the allowed origin to `http://localhost:8080`.
4. Create your users, and optionally groups, in Rauthy.

### Groups and roles

Access control is driven by the claims in each user's token.

- Every user has a private workspace that only they can read and write.
- Group memberships come from the groups claim, named `groups` by default. An entry like `engineering` grants access to that group's shared workspace, and a suffix sets the permission, for example `engineering:read` or `engineering:write`.
- The fixed `admin` role, read from the roles claim (named `roles` by default), grants administrator actions such as maintenance mode and data resets.

Configure your provider to include these claims in the access token.

### Group identity and renaming

A group is identified by the id its groups claim carries, and the claim may spell an entry in either of two ways.

An object following RFC 9068, which encodes each entry as the SCIM shape `{"value": "<id>", "display": "<name>"}`, carries the id and a display name separately.
The id addresses the group in every path (`@<id>/notes.md`) while the display name is used only as a label in the interface, so renaming such a group keeps its shared workspace intact.
Providers that emit bare identifiers such as Entra ID, and those with a claim mapper that can be pointed at the group id such as Keycloak, Authentik, Okta, and Auth0, land here too.

A bare string is all some providers, Rauthy among them, can emit.
There is then no separate id, so the name is the identifier and appears in paths directly.
That works, but renaming such a group leaves its documents behind under the old name, since the new name is indistinguishable from a group that was just created.
Configure your provider to emit the object shape if you want renames to be safe.

## Updating

```bash
docker compose pull
docker compose up -d
```

The backend applies its database migrations automatically on startup, so no separate step is required.

## Production notes

- The example secrets, encryption keys, and passwords are for local testing only. Generate your own for any real deployment.
- Put the stack behind HTTPS, for example by giving the bundled proxy a real domain or by fronting it with your own reverse proxy.
- For GPU-accelerated document processing, uncomment the NVIDIA runtime section in `compose.yaml`.
