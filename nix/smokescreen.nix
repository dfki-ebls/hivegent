{
  lib,
  buildGoModule,
  fetchFromGitHub,
  writeShellScriptBin,
}:
buildGoModule (finalAttrs: {
  pname = "smokescreen";
  version = "0.1.0";
  src = fetchFromGitHub {
    owner = "stripe";
    repo = "smokescreen";
    tag = "v${finalAttrs.version}";
    hash = "sha256-l/HxEhMRFHdxSkWJjYVfPSoTiUWW6MY3us8S/Fl5VV4=";
  };
  vendorHash = null;
  excludedPackages = [ "cmd/check_config" ];
  # TestInvalidHost asserts on the "no such host" resolver error, which needs a
  # DNS server answering NXDOMAIN, while the sandbox has none and refuses the
  # connection instead.
  checkFlags = [ "-skip=^TestInvalidHost$" ];
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
        vendorHash = "sha256-y18ZGf8ZkNnsXNPpjqrrPCLfUJwdROOumUQ+xn6Kavo=";
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
