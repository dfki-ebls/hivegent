{
  lib,
  stdenv,
  callPackage,
  cacert,
  python3,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
  makeBinaryWrapper,
  writeShellApplication,
  coreutils,
  exiftool,
  ffmpeg-headless,
  jq,
  libreoffice,
  pandoc,
  ripgrep,
  tessdata,
  ninja,
}:
let
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
  projectOverlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };
  patchelfOverlay =
    final: prev:
    let
      names = lib.filter (lib.hasPrefix "nvidia-") (lib.attrNames prev) ++ [
        "torch"
        "torchvision"
        "kreuzberg"
      ];
    in
    lib.genAttrs names (
      name:
      prev.${name}.overrideAttrs (_: {
        autoPatchelfIgnoreMissingDeps = true;
      })
    );
  packageOverlay = final: prev: {
    cysignals = prev.cysignals.overrideAttrs (old: {
      buildInputs = (old.buildInputs or [ ]) ++ [ ninja ];
      # cysignals installs its own stack-overflow/signal handlers and refuses
      # to compile with glibc fortification (same fix as nixpkgs' cysignals).
      # The nix cc-wrapper appends -D_FORTIFY_SOURCE after the build system's
      # -U_FORTIFY_SOURCE, so it must be disabled at the wrapper level.
      hardeningDisable = [ "fortify" ];
    });
    hivegent = prev.hivegent.overrideAttrs (old: {
      passthru = lib.recursiveUpdate (old.passthru or { }) {
        tests.pytest = stdenv.mkDerivation {
          name = "${final.hivegent.name}-pytest";
          inherit (final.hivegent) src;
          nativeBuildInputs = [
            cacert
            (mkVenv "hivegent-test-env" {
              hivegent = [
                "all"
                "dev"
              ];
            })
          ]
          ++ runtimeInputs;
          dontConfigure = true;
          buildPhase = ''
            runHook preBuild
            export HOME=$(mktemp -d)
            export NUMBA_CACHE_DIR=$HOME/.numba_cache
            pytest
            runHook postBuild
          '';
          installPhase = ''
            runHook preInstall
            touch "$out"
            runHook postInstall
          '';
        };
      };
    });
  };
  baseSet = callPackage pyproject-nix.build.packages {
    python = python3;
  };
  pythonSet = baseSet.overrideScope (
    lib.composeManyExtensions [
      pyproject-build-systems.overlays.wheel
      projectOverlay
      patchelfOverlay
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

  # docling renders embedded VML/EMF/WMF images by shelling out to a bare
  # `soffice` per image, all sharing one profile under $HOME.  Under the
  # systemd unit $HOME is a persistent StateDirectory, so a `.~lock` left
  # by a crashed/killed run survives restarts and makes every later
  # conversion abort — docling then silently drops the image.  Give each
  # invocation a private, throwaway profile (cleaned up on exit) so runs
  # never collide or inherit a stale lock; $HOME is untouched, keeping the
  # fontconfig cache persistent.
  libreofficeHeadless = writeShellApplication {
    name = "soffice";
    runtimeInputs = [ coreutils ];
    text = ''
      profile="$(mktemp -d)"
      trap 'rm -rf "$profile"' EXIT
      ${lib.getExe' libreoffice "soffice"} -env:UserInstallation="file://$profile" "$@"
    '';
  };

  # - jq, pandoc, ripgrep: used by `hivegent/subprocesses/` wrappers.
  # - ffmpeg: pydub audio decoding (markitdown audio converter, non-wav).
  # - exiftool: optional audio metadata extraction in markitdown.
  # - libreofficeHeadless: docling docx→pdf conversion; Linux-only in nixpkgs.
  runtimeInputs = [
    exiftool
    ffmpeg-headless
    jq
    pandoc
    ripgrep
  ]
  ++ lib.optionals stdenv.hostPlatform.isLinux [ libreofficeHeadless ];
in
app.overrideAttrs (oldAttrs: {
  nativeBuildInputs = (oldAttrs.nativeBuildInputs or [ ]) ++ [ makeBinaryWrapper ];
  # tesserocr (docling OCR) and kreuzberg link their own libtesseract but
  # carry no language data; both resolve it from TESSDATA_PREFIX at runtime
  # (see `nix/tessdata.nix`) — the tesseract CLI itself is not shipped.
  postFixup = (oldAttrs.postFixup or "") + ''
    wrapProgram "$out/bin/hivegent" \
      --prefix PATH : ${lib.makeBinPath runtimeInputs} \
      --set-default TESSDATA_PREFIX ${tessdata} \
      --set-default LOGFIRE_IGNORE_NO_CONFIG 1
  '';
  passthru = (oldAttrs.passthru or { }) // {
    inherit runtimeInputs tessdata;
  };
})
