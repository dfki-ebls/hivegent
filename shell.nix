{
  treefmt,
  watch-dev,
  mkShell,
  nodejs,
  python3,
  uv,
  git,
  jq,
  pandoc,
  ripgrep,
  lib,
}:
mkShell {
  shellHook = ''
    ROOT_DIR="$(${lib.getExe git} rev-parse --show-toplevel)"
    npm --prefix "$ROOT_DIR/frontend" install
    uv --directory "$ROOT_DIR/backend" sync --all-extras
  '';
  HIVEGENT_AUTH_DISABLED = "1";
  UV_PYTHON = lib.getExe python3;
  packages = [
    nodejs
    python3
    treefmt
    uv
    watch-dev
    # CLI tools used by backend subprocess wrappers
    jq
    pandoc
    ripgrep
  ];
}
