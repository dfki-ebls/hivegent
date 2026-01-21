{
  lib,
  callPackage,
  python3,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
}:
let
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
  pyprojectOverlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };
  baseSet = callPackage pyproject-nix.build.packages {
    python = python3;
  };
in
{
  inherit workspace;
  inherit (callPackage pyproject-nix.build.util { }) mkApplication;
  pythonSet = baseSet.overrideScope (
    lib.composeManyExtensions [
      pyproject-build-systems.overlays.wheel
      pyprojectOverlay
    ]
  );
}
