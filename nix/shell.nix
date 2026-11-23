{
  treefmt,
  hivegent,
  backend,
  mkShell,
  nodejs,
  postgresql_18,
  mdbook,
  mdbook-mermaid,
  uv,
  i18next-cli,
  git,
  lib,
  stdenv,
  formats,
}:
mkShell {
  shellHook = ''
    ROOT_DIR="$(${lib.getExe git} rev-parse --show-toplevel)"

    # --- Backend ---
    # Anchored to the repo root so the socket path matches the services-flake
    # `socketDir` regardless of the current directory.  libpq treats a relative
    # host as a TCP name, so the socket dir must be absolute, hence $ROOT_DIR
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

  # === Backend (config.toml like the NixOS module, HIVEGENT_* still override it) ===
  HIVEGENT_CONFIG_FILE = (formats.toml { }).generate "hivegent-config.toml" {
    accept_terms = true;
    debug.enable = true;
    llm = {
      default = "qwen3.8-27b";
      aux = "qwen3.6-35b-a3b";
      providers.vllm = {
        base_url = "http://localhost:18080/v1";
        inference_provider = "vllm";
      };
      models = {
        "qwen3.8-27b" = {
          provider = "vllm";
          model = "qwen3.8-27b";
          reasoning = [
            "low"
            "medium"
            "high"
          ];
          vision = true;
        };
        "qwen3.6-35b-a3b" = {
          provider = "vllm";
          model = "qwen3.6-35b-a3b";
          vision = true;
          chat = false;
        };
      };
    };
    # Monty measures CPU time on Linux alone.
    sandbox.execution_clock = if stdenv.hostPlatform.isLinux then "cpu" else "wall";
    # RAGold's dev server (`npm run dev` in the ragold repo).
    ragold.url = "http://localhost:3002";
  };
  UV_PYTHON = lib.getExe backend.python;
  # tesserocr (in-process docling OCR) resolves tessdata from this prefix.
  TESSDATA_PREFIX = backend.tessdata;
  # Keep dev parity with the wrapped binary: torch.compile needs runtime
  # codegen tools the production sandbox lacks (see `backend/default.nix`).
  DOCLING_INFERENCE_COMPILE_TORCH_MODELS = "0";

  # === Bridge (web-adapter debug against `hivegent dev`; no OIDC/Teams) ===
  BRIDGE_CONFIG_FILE = (formats.json { }).generate "bridge-config.json" {
    backendUrl = "http://127.0.0.1:8000";
    adapters = {
      teams = false;
      web = true;
    };
  };

  packages = [
    nodejs
    i18next-cli
    backend.python
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
