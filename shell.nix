{
  treefmt,
  watch-dev,
  mkShell,
  nodejs,
  python3,
  uv,
  git,
  lib,
  writeShellScriptBin,
}:
mkShell {
  shellHook = ''
    npm install
    uv sync --all-extras --locked
  '';
  UV_PYTHON = lib.getExe python3;
  packages = [
    (writeShellScriptBin "uv" ''
      exec ${lib.getExe uv} \
        --directory "$(${lib.getExe git} rev-parse --show-toplevel)/backend" \
        "$@"
    '')
    (writeShellScriptBin "npm" ''
      exec ${lib.getExe' nodejs "npm"} \
        --prefix "$(${lib.getExe git} rev-parse --show-toplevel)/frontend" \
        "$@"
    '')
    nodejs
    python3
    treefmt
    watch-dev
  ];
}
