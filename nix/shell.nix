{
  treefmt,
  hivegent,
  backend,
  mkShell,
  nodejs,
  python313,
  postgresql_18,
  mdbook,
  mdbook-mermaid,
  uv,
  git,
  lib,
}:
mkShell {
  shellHook = ''
    ROOT_DIR="$(${lib.getExe git} rev-parse --show-toplevel)"

    # --- Backend ---
    # Anchored to the repo root so the socket path matches the services-flake
    # `socketDir` regardless of the current directory.  libpq treats a relative
    # host as a TCP name, so the socket dir must be absolute — hence $ROOT_DIR
    # rather than a hardcoded path.
    export HIVEGENT_DB__URL="postgresql+psycopg:///hivegent?host=$ROOT_DIR/data/db"
    uv --directory "$ROOT_DIR/backend" sync --all-extras

    # --- Frontend ---
    npm --prefix "$ROOT_DIR/frontend" install

    # --- Bridge ---
    # Chat SDK state (node-postgres) shares the dev Postgres over the same socket,
    # in its own `chat_state_*` tables (keyPrefix `hivegent-bridge`).
    export POSTGRES_URL="postgresql:///hivegent?host=$ROOT_DIR/data/db"
    npm --prefix "$ROOT_DIR/bridge" install
  '';

  # === Backend (HIVEGENT_* pydantic settings + Python/OCR toolchain) ===
  HIVEGENT_AUTH__ENABLE = "0";
  HIVEGENT_AUTH__ALLOW_DISABLED = "1";
  # SSRF policy is independent of auth: a dev shell that talks to a
  # localhost LLM/MCP server must opt into private URLs explicitly.
  HIVEGENT_SECURITY__ALLOW_PRIVATE_URLS = "1";
  HIVEGENT_LOGFIRE__ENABLE = "0";
  HIVEGENT_LLM__MODEL = "qwen3.6-35b-a3b";
  HIVEGENT_LLM__AUX_MODEL = "qwen3.5-0.8b";
  HIVEGENT_LLM__BASE_URL = "http://localhost:18000/v1";
  HIVEGENT_LLM__INFERENCE_PROVIDER = "llama.cpp";
  UV_PYTHON = lib.getExe python313;
  # tesserocr (in-process docling OCR) resolves tessdata from this prefix.
  TESSDATA_PREFIX = backend.tessdata;
  # Keep dev parity with the wrapped binary: torch.compile needs runtime
  # codegen tools the production sandbox lacks (see `backend/default.nix`).
  DOCLING_INFERENCE_COMPILE_TORCH_MODELS = "0";

  # === Frontend (Vite) ===
  VITE_FEATURE_ALL = "false";

  # === Bridge (web-adapter debug against the auth-disabled backend; no OIDC/Teams) ===
  HIVEGENT_URL = "http://127.0.0.1:8000";
  BOT_USERNAME = "hivegent";
  ENABLE_TEAMS = "false";
  ENABLE_WEB = "true";

  packages = [
    nodejs
    python313
    treefmt
    uv
    hivegent
    postgresql_18
    mdbook
    mdbook-mermaid
  ]
  # CLI tools used by backend subprocess wrappers + docling deps; sourced
  # from the backend derivation so the dev shell and the wrapped binary
  # share a single list (see `backend/default.nix`).
  ++ backend.runtimeInputs;
}
