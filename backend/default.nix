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
  autoAddDriverRunpath,
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
  gcc,
  openssl,
  # Whether docling may wrap its torch models (picture classifier &c.) in
  # torch.compile.  TorchInductor does runtime codegen through external
  # tools (verified: gcc and openssl suffice), so enabling this puts them
  # on the wrapper's PATH.  The toolchain must stay ABI-compatible with
  # the torch wheels pinned in uv.lock, which is why the switch lives here
  # and not in deployments.  The GPU path additionally relies on the
  # triton wheel already present in the closure.  Disabled by default:
  # eager inference is fast enough for these small models, and compilation
  # would otherwise be repaid on every service restart.  Only honored on
  # Linux — inductor is untested on darwin dev machines and the deployment
  # target is Linux.
  enableTorchCompile ? false,
}:
let
  torchCompile = enableTorchCompile && stdenv.hostPlatform.isLinux;
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
  projectOverlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };
  patchelfOverlay =
    final: prev:
    let
      names =
        lib.filter (lib.hasPrefix "nvidia-") (lib.attrNames prev)
        ++ lib.filter (name: prev ? ${name}) [
          "torch"
          "torchvision"
          "triton"
        ];
    in
    lib.genAttrs names (
      name:
      prev.${name}.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ autoAddDriverRunpath ];
        autoPatchelfIgnoreMissingDeps = [
          # NVIDIA driver lib, the only one injected at runtime (autoAddDriverRunpath)
          "libcuda.so.1"
          # bundled CUDA runtime + torch libs, resolved from the merged virtualenv
          "libcudart.so.*"
          "libcublas.so.*"
          "libcublasLt.so.*"
          "libcudnn.so.*"
          "libcufft.so.*"
          "libcufile.so.*"
          "libcupti.so.*"
          "libcurand.so.*"
          "libcusolver.so.*"
          "libcusparse.so.*"
          "libcusparseLt.so.*"
          "libnvrtc.so.*"
          "libnvJitLink.so.*"
          "libnccl.so.*"
          "libnvshmem_host.so.*"
          "libc10*.so"
          "libtorch*.so"
          # optional NVSHMEM/cuFile transports (RDMA/UCX/libfabric/MPI), not provided
          "libibverbs.so.*"
          "librdmacm.so.*"
          "libmlx5.so.*"
          "libucp.so.*"
          "libucs.so.*"
          "libfabric.so.*"
          "libmpi.so.*"
          "liboshmem.so.*"
          "libpmix.so.*"
        ];
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
  ++ lib.optionals stdenv.hostPlatform.isLinux [ libreofficeHeadless ]
  ++ lib.optionals torchCompile [
    gcc
    openssl
  ];
in
app.overrideAttrs (oldAttrs: {
  nativeBuildInputs = (oldAttrs.nativeBuildInputs or [ ]) ++ [ makeBinaryWrapper ];
  # tesserocr (docling OCR) and kreuzberg link their own libtesseract but
  # carry no language data; both resolve it from TESSDATA_PREFIX at runtime
  # (see `nix/tessdata.nix`) — the tesseract CLI itself is not shipped.
  #
  # DOCLING_INFERENCE_COMPILE_TORCH_MODELS tracks `enableTorchCompile`:
  # without the toolchain on PATH, docling's default of compiling its torch
  # models would make every PDF conversion die in the enrichment stage
  # (TorchInductor cannot codegen).  `--set-default` keeps both env vars
  # overridable from the unit or shell.
  postFixup = (oldAttrs.postFixup or "") + ''
    wrapProgram "$out/bin/hivegent" \
      --prefix PATH : ${lib.makeBinPath runtimeInputs} \
      --set-default TESSDATA_PREFIX ${tessdata} \
      --set-default DOCLING_INFERENCE_COMPILE_TORCH_MODELS ${if torchCompile then "1" else "0"} \
      --set-default LOGFIRE_IGNORE_NO_CONFIG 1
  '';
  passthru = (oldAttrs.passthru or { }) // {
    inherit runtimeInputs tessdata;
    enableTorchCompile = torchCompile;
  };
})
