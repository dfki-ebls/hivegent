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
    rev = "f03c477dc9f4fe3252ae96ad9274d53108b2c53c";
    hash = "sha256-G06cIrhHYV/j+gbC5aqqMsuljUIU67EwzxiUqvZfk9w=";
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
    license = lib.licenses.asl20;
    mainProgram = "smokescreen";
  };
})
