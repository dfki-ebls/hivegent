{
  buildNpmPackage,
  importNpmLock,
  lib,
  # Slash-terminated URL the handbook is mounted at, or "" to omit the SPA's
  # "Documentation" link. Mirrors the `docs` package's `sitePath`.
  docsUrl ? "",
}:
buildNpmPackage (finalAttrs: {
  inherit (finalAttrs.npmDeps) pname version;
  inherit (importNpmLock) npmConfigHook;
  npmDeps = importNpmLock { npmRoot = ./.; };

  VITE_DOCS_URL = docsUrl;

  # The canonical logo lives at the repo-level `assets/` and is symlinked into
  # `public/`, so the build source must span both directories for the symlink
  # to resolve in the sandbox.
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ./.
      ../assets/logo.svg
    ];
  };
  sourceRoot = "${finalAttrs.src.name}/frontend";

  doCheck = true;
  checkPhase = ''
    runHook preCheck
    npm test
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p "$out"
    cp -r "dist/." "$out"

    runHook postInstall
  '';

  meta = with lib; {
    license = licenses.mit;
    maintainers = with maintainers; [ mirkolenz ];
  };
})
