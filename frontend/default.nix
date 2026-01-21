{
  buildNpmPackage,
  importNpmLock,
  lib,
  biome,
}:
let
in
buildNpmPackage (finalAttrs: {
  inherit (finalAttrs.npmDeps) pname version;
  inherit (importNpmLock) npmConfigHook;
  npmDeps = importNpmLock { npmRoot = finalAttrs.src; };

  src = ./.;
  installPhase = ''
    runHook preInstall

    mkdir -p "$out"
    cp -r "dist/." "$out"

    runHook postInstall
  '';

  BIOME_BINARY = lib.getExe biome;

  meta = with lib; {
    license = licenses.mit;
    maintainers = with maintainers; [ mirkolenz ];
  };
})
