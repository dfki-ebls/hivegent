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
# api_key = "..."   # only if your provider requires one

[auth]
issuer = "http://auth.localhost:8081/auth/v1"
audience = ["hivegent-*"]
```

The most important settings:

- `llm.model`: the main chat model, which needs a large context window and tool calling.
- `llm.aux_model`: a small, fast, vision-capable model used for document conversion, captions, and titles. It falls back to the main model when unset.
- `llm.base_url` and `llm.api_key`: your OpenAI-compatible endpoint.
- `db.url`: the PostgreSQL connection string. The bundled database already has pgvector enabled.
- `auth.issuer`: the OIDC provider's issuer URL.
- `auth.audience`: the token audiences to accept. The entry `hivegent-*` accepts every current and future Hivegent client.

Common extras are `HIVEGENT_TOOLS__ENABLE_WEB=true` to allow web search, `HIVEGENT_EMBEDDING__MODEL` to change the embedding model, and `HIVEGENT_MCP__ENABLE=true` to expose the MCP endpoint.

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
