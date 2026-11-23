{
  buildNpmPackage,
  importNpmLock,
  lib,
}:
buildNpmPackage (finalAttrs: {
  inherit (finalAttrs.npmDeps) pname version;
  inherit (importNpmLock) npmConfigHook;
  npmDeps = importNpmLock { npmRoot = ./.; };

  # The canonical logo lives at the repo-level `assets/` and is symlinked into
  # `public/`, and `vite.config.ts` reads the repo-level license
  # and additional terms, and the tests read the shared RAGold schema,
  # so the build source must span the repo root.
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ./.
      ../assets/logo.svg
      ../LICENSE
      ../TERMS
      ../schemas/ragold.json
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
