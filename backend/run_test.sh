#!/usr/bin/env bash
# Runs hivegent.test_dyn with the native libs the PyPI wheels (numpy, torch,
# opencv) need. The venv runs on Nix's Python, whose loader never searches the
# host's /usr/lib, so they come from the flake's pinned nixpkgs — the same list
# as the dev backend's LD_LIBRARY_PATH in `nix/processes.nix`.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$BACKEND_DIR")"

mapfile -t lib_pkgs < <(
  nix build --impure --no-link --print-out-paths --expr "
    let
      flake = builtins.getFlake \"git+file://$ROOT_DIR\";
      pkgs = flake.inputs.nixpkgs.legacyPackages.\${builtins.currentSystem};
    in
    with pkgs; [ stdenv.cc.cc.lib zlib libxcb libGL glib ]
  "
)

LD_LIBRARY_PATH="$(printf '%s/lib:' "${lib_pkgs[@]}")${LD_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH

exec uv --project "$BACKEND_DIR" run python -m hivegent.test_dyn "$@"
