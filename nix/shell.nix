{
  treefmt,
  hivegent,
  backend,
  mkShell,
  nodejs,
  python3,
  postgresql_18,
  uv,
  git,
  lib,
}:
mkShell {
  shellHook = ''
    ROOT_DIR="$(${lib.getExe git} rev-parse --show-toplevel)"
    # Anchored to the repo root so the socket path matches the services-flake
    # `socketDir` regardless of the current directory.  libpq treats a relative
    # host as a TCP name, so the socket dir must be absolute — hence $ROOT_DIR
    # rather than a hardcoded path.
    export HIVEGENT_DB__URL="postgresql+psycopg:///hivegent?host=$ROOT_DIR/data/db"
    npm --prefix "$ROOT_DIR/frontend" install
    uv --directory "$ROOT_DIR/backend" sync --all-extras
  '';
  HIVEGENT_AUTH__ENABLE = "0";
  HIVEGENT_AUTH__ALLOW_DISABLED = "1";
  # SSRF policy is independent of auth: a dev shell that talks to a
  # localhost LLM/MCP server must opt into private URLs explicitly.
  HIVEGENT_SECURITY__ALLOW_PRIVATE_URLS = "1";
  HIVEGENT_LOGFIRE__ENABLE = "0";
  HIVEGENT_LLM__MODEL = "qwen3.6-35b-a3b";
  HIVEGENT_LLM__AUX_MODEL = "qwen3.5-0.8b";
  HIVEGENT_LLM__BASE_URL = "http://localhost:18000/v1";
  VITE_FEATURE_ALL = "false";
  UV_PYTHON = lib.getExe python3;
  packages = [
    nodejs
    python3
    treefmt
    uv
    hivegent
    postgresql_18
  ]
  # CLI tools used by backend subprocess wrappers + docling deps; sourced
  # from the backend derivation so the dev shell and the wrapped binary
  # share a single list (see `backend/default.nix`).
  ++ backend.runtimeInputs;
}
