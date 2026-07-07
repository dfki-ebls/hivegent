{
  lib,
  buildNpmPackage,
  importNpmLock,
}:
buildNpmPackage (finalAttrs: {
  inherit (finalAttrs.npmDeps) pname version;
  inherit (importNpmLock) npmConfigHook;
  npmDeps = importNpmLock { npmRoot = ./.; };

  src = ./.;

  # `npm run build` (tsc) emits `dist/`; the package.json `bin` points at
  # `dist/index.js`, whose shebang buildNpmPackage patches to the store node.
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    npm test
    runHook postCheck
  '';

  meta = {
    description = "Multi-platform chat bridge exposing the hivegent backend via the Vercel Chat SDK";
    mainProgram = "hivegent-bridge";
    license = lib.licenses.mit;
    maintainers = with lib.maintainers; [ mirkolenz ];
    platforms = lib.platforms.all;
  };
})
