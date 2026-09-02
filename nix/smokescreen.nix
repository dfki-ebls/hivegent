{
  lib,
  buildGoModule,
  fetchFromGitHub,
  writeShellScriptBin,
}:
buildGoModule (finalAttrs: {
  pname = "smokescreen";
  version = "unstable-2026-08-19";
  src = fetchFromGitHub {
    owner = "stripe";
    repo = "smokescreen";
    rev = "d4da883a671475551d78da812db547341c8fe6c3";
    hash = "sha256-wv+5OhpIdLX0qjSSPmxq5/Z4ecJVVxT8WSDTMPCYRug=";
  };
  vendorHash = null;
  subPackages = [ "." ];
  tags = [ "nointegration" ];
  checkPhase = ''
    runHook preCheck
    # TestInvalidHost asserts on the "no such host" resolver error, which needs a
    # DNS server answering NXDOMAIN, while the sandbox has none and refuses the
    # connection instead.
    go test -tags=${lib.concatStringsSep "," finalAttrs.tags} \
      -skip '^TestInvalidHost$' -p "$NIX_BUILD_CORES" ./...
    runHook postCheck
  '';
  passthru = {
    defaultPort = 4750;
    # `nix/sbom.nix` detects the module licenses, which means fetching each
    # module: Go can serve neither its graph nor its licenses out of a `vendor/`
    # tree, so this cache is the proxy that serves them offline.  Only
    # `.goModules` is ever realised, so no second binary is built, and the
    # shipped one still compiles from the `vendor/` directory upstream
    # committed -- which the cache has to drop, `buildGoModule` refusing to
    # combine the two.
    goModuleCache =
      (finalAttrs.finalPackage.overrideAttrs {
        proxyVendor = true;
        vendorHash = "sha256-IhKwjzoOzoe66Yh6dOX6MHQTEzjLmpEbVg9GL0NnUcQ=";
        postPatch = "rm -rf vendor";
      }).goModules;
    # The proxy must never be reachable off-host, and its listen address is
    # also the URL the backend dials, so both are derived here from one port
    # instead of being restated at each deployment.
    onLoopback = port: {
      url = "http://127.0.0.1:${toString port}";
      package = writeShellScriptBin finalAttrs.meta.mainProgram ''
        exec ${lib.getExe finalAttrs.finalPackage} \
          ${
            lib.cli.toCommandLineShellGNU { } {
              listen-ip = "127.0.0.1";
              listen-port = port;
            }
          } "$@"
      '';
    };
  };
  meta = {
    description = "HTTP CONNECT proxy that prevents SSRF";
    homepage = "https://github.com/stripe/smokescreen";
    license = lib.licenses.mit;
    mainProgram = "smokescreen";
  };
})
