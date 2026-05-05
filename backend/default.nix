{
  lib,
  callPackage,
  python3,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
  makeBinaryWrapper,
  jq,
  pandoc,
  ripgrep,
  libreoffice,
  stdenv,
}:
let
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
  projectOverlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };
  getCudaPkgs = attrs: lib.filter (name: lib.hasPrefix "nvidia-" name) (lib.attrNames attrs);
  cudaOverlay =
    final: prev:
    lib.genAttrs (getCudaPkgs prev) (
      name:
      prev.${name}.overrideAttrs (old: {
        autoPatchelfIgnoreMissingDeps = true;
      })
    );
  buildSystemOverlay =
    final: prev:
    lib.mapAttrs
      (
        name: value:
        prev.${name}.overrideAttrs (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ (final.resolveBuildSystem value);
        })
      )
      {
        antlr4-python3-runtime.setuptools = [ ];
        ebooklib.setuptools = [ ];
        pylatexenc.setuptools = [ ];
      };
  packageOverlay =
    final: prev:
    lib.mapAttrs (name: value: prev.${name}.overrideAttrs value) {
      torch = old: {
        autoPatchelfIgnoreMissingDeps = true;
      };
      torchvision = old: {
        autoPatchelfIgnoreMissingDeps = true;
      };
      kreuzberg = old: {
        autoPatchelfIgnoreMissingDeps = true;
      };
    };
  baseSet = callPackage pyproject-nix.build.packages {
    python = python3;
  };
  pythonSet = baseSet.overrideScope (
    lib.composeManyExtensions [
      pyproject-build-systems.overlays.wheel
      projectOverlay
      cudaOverlay
      buildSystemOverlay
      packageOverlay
    ]
  );
  mkVenv =
    name: deps:
    (pythonSet.mkVirtualEnv name deps).overrideAttrs (_: {
      venvIgnoreCollisions = [
        "${python3.sitePackages}/cv2/*"
      ];
    });
  inherit (callPackage pyproject-nix.build.util { }) mkApplication;

  app = mkApplication {
    venv = mkVenv "hivegent-env" workspace.deps.optionals;
    package = pythonSet.hivegent;
  };

  # Runtime CLI tools the backend invokes via `asyncio.create_subprocess_exec`
  # (`hivegent/subprocesses/`) plus libreoffice, used by docling for office
  # document conversion. Exposed via `passthru.runtimeInputs` so `shell.nix`
  # can use the same list and stay in sync without redeclaring it.
  runtimeInputs = [
    jq
    pandoc
    ripgrep
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [ libreoffice ];
in
app.overrideAttrs (oldAttrs: {
  nativeBuildInputs = (oldAttrs.nativeBuildInputs or [ ]) ++ [ makeBinaryWrapper ];
  postFixup =
    (oldAttrs.postFixup or "")
    + ''
      wrapProgram "$out/bin/hivegent" \
        --prefix PATH : ${lib.makeBinPath runtimeInputs}
    '';
  passthru = (oldAttrs.passthru or { }) // {
    inherit runtimeInputs;
  };
})
