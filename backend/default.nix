{
  lib,
  stdenv,
  callPackage,
  python3,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
  makeBinaryWrapper,
  exiftool,
  ffmpeg-headless,
  jq,
  libreoffice,
  pandoc,
  ripgrep,
  tesseract,
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

  # Every external CLI any code path in the backend or its dependencies can
  # invoke. Declared explicitly (rather than relying on PATH) so the wrapped
  # binary behaves identically across machines. Exposed via
  # `passthru.runtimeInputs` so `shell.nix` reuses the same list.
  #
  # - jq, pandoc, ripgrep: used by `hivegent/subprocesses/` wrappers.
  # - ffmpeg: pydub audio decoding (markitdown audio converter, non-wav).
  # - exiftool: optional audio metadata extraction in markitdown.
  # - tesseract: optional OCR backend for docling (`TesseractCliOcrOptions`).
  # - libreoffice: docling docx→pdf conversion; Linux-only in nixpkgs.
  runtimeInputs = [
    exiftool
    ffmpeg-headless
    jq
    pandoc
    ripgrep
    tesseract
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [ libreoffice ];
in
app.overrideAttrs (oldAttrs: {
  nativeBuildInputs = (oldAttrs.nativeBuildInputs or [ ]) ++ [ makeBinaryWrapper ];
  postFixup = (oldAttrs.postFixup or "") + ''
    wrapProgram "$out/bin/hivegent" \
      --prefix PATH : ${lib.makeBinPath runtimeInputs}
  '';
  passthru = (oldAttrs.passthru or { }) // {
    inherit runtimeInputs;
  };
})
