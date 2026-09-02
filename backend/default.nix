{
  lib,
  stdenv,
  callPackage,
  cacert,
  python313,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
  makeBinaryWrapper,
  autoAddDriverRunpath,
  writeShellApplication,
  symlinkJoin,
  coreutils,
  exiftool,
  ffmpeg-headless,
  jq,
  libreoffice,
  pandoc,
  poppler-utils,
  ripgrep,
  tessdata,
  ninja,
  pkg-config,
  leptonica,
  tesseract,
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
    # Built from the sdist (darwin, see `pyproject.toml`), tesserocr resolves the
    # libraries it links through `pkg-config tesseract lept` rather than bundling
    # them as the wheels do.  Gated on the source form, not the platform, since
    # that is what varies, and a wheel build would otherwise hand these to
    # `autoPatchelfHook` as RPATH candidates for libraries it already carries.
    tesserocr = prev.tesserocr.overrideAttrs (
      old:
      lib.optionalAttrs (old.passthru.format == "pyproject") {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ pkg-config ];
        buildInputs = (old.buildInputs or [ ]) ++ [
          leptonica
          tesseract
        ];
      }
    );
    hivegent-backend = prev.hivegent-backend.overrideAttrs (old: {
      passthru = lib.recursiveUpdate (old.passthru or { }) {
        tests.pytest = stdenv.mkDerivation {
          name = "${final.hivegent-backend.name}-pytest";
          inherit (final.hivegent-backend) src;
          nativeBuildInputs = [
            cacert
            (mkVenv "hivegent-test-env" {
              hivegent-backend = [
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
    python = python313;
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
    # The version belongs in the name: the venv reaches the closure only through
    # the wrapper's string context, so bombon reads it back off the store path
    # and skips what states no version, which would strand `python3` in the SBOM
    # with nothing depending on it.
    (pythonSet.mkVirtualEnv "${name}-${pythonSet.hivegent-backend.version}" deps).overrideAttrs (_: {
      venvIgnoreCollisions = [
        "${python313.sitePackages}/cv2/*"
      ];
    });
  inherit (callPackage pyproject-nix.build.util { }) mkApplication;

  venv = mkVenv "hivegent-env" workspace.deps.optionals;

  # Every Python dependency as its own uv2nix derivation, i.e. what the venv
  # symlinks into.  Neither it nor `venv` is computable outside this scope.
  venvPackages = pythonSet.resolveVirtualEnv workspace.deps.optionals;

  app = mkApplication {
    inherit venv;
    package = pythonSet.hivegent-backend;
  };

  # docling renders embedded VML/EMF/WMF images by shelling out to LibreOffice
  # per image, all sharing one profile under $HOME.  Under the systemd unit
  # $HOME is a persistent StateDirectory, so a `.~lock` left by a crashed or
  # killed run survives restarts and makes every later conversion abort —
  # docling then silently drops the image.  Give each invocation a private,
  # throwaway profile (cleaned up on exit) so runs never collide or inherit a
  # stale lock; $HOME is untouched, keeping the fontconfig cache persistent.
  #
  # docling resolves the binary as `which("libreoffice") or which("soffice")`,
  # so `libreoffice` is the canonical wrapper name (probed first, ahead of any
  # bare `libreoffice` later on the unit's PATH), with `soffice` a symlinked
  # alias for callers using that name.
  libreofficeWrapper = writeShellApplication {
    name = "libreoffice";
    runtimeInputs = [ coreutils ];
    text = ''
      profile="$(mktemp -d)"
      trap 'rm -rf "$profile"' EXIT
      ${lib.getExe libreoffice} -env:UserInstallation="file://$profile" "$@"
    '';
  };
  libreofficeHeadless = symlinkJoin {
    name = "libreoffice-headless";
    paths = [ libreofficeWrapper ];
    postBuild = ''
      ln -s libreoffice "$out/bin/soffice"
    '';
  };

  # - jq, pandoc, poppler-utils, ripgrep: used by `hivegent/subprocesses/` wrappers.
  # - poppler-utils (pdftotext): PDF text-recovery fallback for legacy PDFs whose
  #   fonts carry no ToUnicode CMap, which docling's backend dumps as raw glyph
  #   names — poppler reconstructs the text from the glyph-name convention.
  # - ffmpeg: pydub audio decoding (markitdown audio converter, non-wav).
  # - exiftool: optional audio metadata extraction in markitdown.
  # - libreofficeHeadless: docling docx→pdf conversion; Linux-only in nixpkgs.
  runtimeInputs = [
    exiftool
    ffmpeg-headless
    jq
    pandoc
    poppler-utils
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
  # `project.license` in `pyproject.toml` does not reach here: pyproject-nix's
  # meta renderer reads only the PEP 621 `license.text` table, not the PEP 639
  # string, so the license is stated in both spellings.
  meta = (oldAttrs.meta or { }) // {
    license = lib.licenses.mit;
    maintainers = with lib.maintainers; [ mirkolenz ];
  };
  # tesserocr (docling OCR) and kreuzberg link their own libtesseract but
  # carry no language data; both resolve it from TESSDATA_PREFIX at runtime
  # (see `nix/tessdata.nix`) — the tesseract CLI itself is not shipped.
  #
  # DOCLING_INFERENCE_COMPILE_TORCH_MODELS tracks `enableTorchCompile`:
  # without the toolchain on PATH, docling's default of compiling its torch
  # models would make every PDF conversion die in the enrichment stage
  # (TorchInductor cannot codegen).
  #
  # DBUS_SESSION_BUS_ADDRESS: docling shells out to LibreOffice, whose nixpkgs
  # wrapper otherwise starts a private D-Bus daemon under `/run/user/$UID` —
  # unwritable for an unprivileged/DynamicUser service (no logind session), and
  # fatal to the conversion.  Headless conversion needs no session bus.
  #
  # `--set-default` keeps every env var overridable from the unit or shell.
  postFixup = (oldAttrs.postFixup or "") + ''
    wrapProgram "$out/bin/hivegent" \
      --prefix PATH : ${lib.makeBinPath runtimeInputs} \
      --set-default TESSDATA_PREFIX ${tessdata} \
      --set-default DOCLING_INFERENCE_COMPILE_TORCH_MODELS ${if torchCompile then "1" else "0"} \
      --set-default DBUS_SESSION_BUS_ADDRESS "disabled:" \
      --set-default LOGFIRE_IGNORE_NO_CONFIG 1
  '';
  passthru = (oldAttrs.passthru or { }) // {
    # `nix/sbom.nix` names the SBOM's root component from it, since a venv scan
    # describes the dependencies and not the project they belong to.
    pyproject = ./pyproject.toml;
    inherit
      runtimeInputs
      tessdata
      venv
      venvPackages
      ;
    enableTorchCompile = torchCompile;
  };
})
