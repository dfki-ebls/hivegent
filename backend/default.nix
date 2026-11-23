{
  lib,
  stdenv,
  callPackage,
  python314,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
  makeBinaryWrapper,
  cacert,
  jq,
  pandoc,
  poppler-utils,
  ripgrep,
  tessdata,
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
  # and not in deployments.  Disabled by default:
  # eager inference is fast enough for these small models, and compilation
  # would otherwise be repaid on every service restart.  Only honored on
  # Linux, inductor is untested on darwin dev machines and the deployment
  # target is Linux.
  enableTorchCompile ? false,
  # Project directories of plugins, Python packages with a `pyproject.toml` that
  # advertise `hivegent.plugins` entry points.  Each joins the backend's venv,
  # so it runs on the backend's interpreter, and they load only once
  # `plugins.enabled` names them.
  plugins ? [ ],
}:
let
  torchCompile = enableTorchCompile && stdenv.hostPlatform.isLinux;
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
  inherit (pyproject-nix.lib)
    pep440
    pep508
    pep621
    pypa
    ;
  # A plugin joins the venv and takes its dependencies from the backend's own
  # resolution, so one the backend lacks or holds at a version the plugin
  # refuses stops the evaluation, and a module clashing with another package
  # fails the venv's collision check.  A plugin's dependency the backend lacks
  # is added to the backend's `pyproject.toml` and lock.
  pluginProjects = map (
    projectRoot: pyproject-nix.lib.project.loadPyproject { inherit projectRoot; }
  ) plugins;
  pluginName = project: pypa.normalizePackageName project.pyproject.project.name;
  pluginOverlay =
    final: _prev:
    lib.listToAttrs (
      map (
        project:
        lib.nameValuePair (pluginName project) (
          final.callPackage (
            {
              stdenv,
              pyprojectHook,
              resolveBuildSystem,
            }:
            stdenv.mkDerivation (
              pyproject-nix.build.lib.renderers.mkDerivation {
                inherit project;
                environ = pep508.mkEnviron final.python;
              } { inherit pyprojectHook resolveBuildSystem; }
            )
          ) { }
        )
      ) pluginProjects
    );
  pluginConflicts =
    project:
    lib.concatMap
      (
        dep:
        let
          name = pypa.normalizePackageName dep.name;
          held = pythonSet.${name};
          version = pep440.parseVersion held.version;
        in
        if !(pythonSet ? ${name}) then
          [ "${pluginName project} needs ${name}, which the backend does not install" ]
        else
          lib.optional (
            !lib.all (cond: pep440.comparators.${cond.op} version cond.version) dep.conditions
          ) "${pluginName project} refuses ${name} ${held.version}"
      )
      (pep621.filterDependenciesByEnviron (pep508.mkEnviron pythonSet.python) [ ] project.dependencies)
      .dependencies;
  venvDeps = workspace.deps.optionals // lib.genAttrs (map pluginName pluginProjects) (_: [ ]);
  projectOverlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
  packageOverlay = final: prev: {
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
    # `license-files` in `pyproject.toml` reaches the repo-level license
    # and additional terms through symlinks, and the tests read the shared
    # RAGold schema and the turn fixtures the frontend replays, so the build
    # source must span the repo root.
    hivegent-backend = prev.hivegent-backend.overrideAttrs (
      finalAttrs: old: {
        src = lib.fileset.toSource {
          root = ../.;
          fileset = lib.fileset.unions [
            ./.
            ../LICENSE
            ../TERMS
            ../schemas/ragold.json
            ../frontend/src/test/fixtures/turns
          ];
        };
        sourceRoot = "${finalAttrs.src.name}/backend";
        passthru = lib.recursiveUpdate (old.passthru or { }) {
          tests.pytest = stdenv.mkDerivation {
            name = "${final.hivegent-backend.name}-pytest";
            inherit (final.hivegent-backend) src sourceRoot;
            nativeBuildInputs = [
              (mkVenv "hivegent-test-env" {
                hivegent-backend = [
                  "all"
                  "dev"
                ];
              })
            ]
            ++ runtimeInputs
            # httpx2 reads `SSL_CERT_FILE` up front, which the sandbox points at nothing.
            ++ [
              cacert
              pandoc
            ];
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
      }
    );
  };
  baseSet = callPackage pyproject-nix.build.packages {
    python = python314;
  };
  pythonSet = baseSet.overrideScope (
    lib.composeManyExtensions [
      pyproject-build-systems.overlays.wheel
      projectOverlay
      packageOverlay
      pluginOverlay
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
        "${pythonSet.python.sitePackages}/cv2/*"
      ];
    });
  inherit (callPackage pyproject-nix.build.util { }) mkApplication;

  venv =
    let
      conflicts = lib.concatMap pluginConflicts pluginProjects;
    in
    lib.throwIf (conflicts != [ ]) "Plugin dependency conflicts: ${lib.concatStringsSep ", " conflicts}"
      (mkVenv "hivegent-env" venvDeps);

  # Every Python dependency as its own uv2nix derivation, i.e. what the venv
  # symlinks into.  Neither it nor `venv` is computable outside this scope.
  venvPackages = pythonSet.resolveVirtualEnv venvDeps;

  app = mkApplication {
    inherit venv;
    package = pythonSet.hivegent-backend;
  };

  # - jq, ripgrep: used by `exar.subprocess`, through the `Ripgrep` engine of `exar.tools.files.Grep` and `exar.tools.files.Jq`.
  # - poppler-utils: `pdftotext`, which exar invokes for PDFs.
  # - pandoc is left out: it links the GPL-2.0-only texmath and skylighting,
  #   which the flake could not serve as source under GPL-3.0 (`nix/notices.nix`).
  #   Its converter stays, tested against the binary but unavailable in builds.
  # - poppler-utils (pdftotext): PDF text-recovery fallback for legacy PDFs whose
  #   fonts carry no ToUnicode CMap, which docling's backend dumps as raw glyph
  #   names, poppler reconstructs the text from the glyph-name convention.
  runtimeInputs = [
    jq
    poppler-utils
    ripgrep
  ]
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
  # (see `nix/tessdata.nix`), the tesseract CLI itself is not shipped.
  #
  # DOCLING_INFERENCE_COMPILE_TORCH_MODELS tracks `enableTorchCompile`:
  # without the toolchain on PATH, docling's default of compiling its torch
  # models would make every PDF conversion die in the enrichment stage
  # (TorchInductor cannot codegen).
  #
  # `--set-default` keeps every env var overridable from the unit or shell.
  postFixup = (oldAttrs.postFixup or "") + ''
    wrapProgram "$out/bin/hivegent" \
      --prefix PATH : ${lib.makeBinPath runtimeInputs} \
      --set-default TESSDATA_PREFIX ${tessdata} \
      --set-default DOCLING_INFERENCE_COMPILE_TORCH_MODELS ${if torchCompile then "1" else "0"} \
      --set-default LOGFIRE_IGNORE_NO_CONFIG 1
  '';
  passthru = (oldAttrs.passthru or { }) // {
    # `nix/sbom.nix` names the SBOM's root component from it, since a venv scan
    # describes the dependencies and not the project they belong to.
    pyproject = ./pyproject.toml;
    # The interpreter the venv runs on.
    inherit (pythonSet) python;
    inherit
      runtimeInputs
      tessdata
      venv
      venvPackages
      plugins
      ;
    enableTorchCompile = torchCompile;
  };
})
