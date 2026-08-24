# All-in-one container: FastAPI backend, outbound proxy, and Caddy/SPA in one image,
# supervised by dinit as PID 1. Secure by default — unprivileged `nobody` uid, no
# shell, no `--privileged` — with a baked healthcheck across the chain (Caddy ->
# API). Caddy serves plain HTTP; front it with a TLS terminator for public use.
# Build-time choices (name/tag, ports, data path, bundles) are `docker.override`
# arguments. The database stays external (pgvector image, see `compose.yaml`).
#
# dinit not systemd so the image runs unprivileged anywhere. Its `--container`
# mode exits on SIGTERM (from `docker stop`) after stopping every service, and it
# reads service descriptions read-only from the store. The service wiring mirrors
# the NixOS systemd unit (`nixos/service.nix`).
{
  lib,
  dockerTools,
  writeText,
  linkFarm,
  tzdata,
  curlMinimal,
  caddy,
  dinit,
  backend,
  frontend,
  smokescreen,
  # Overridable mdbook handbook package bundled into the image, or `null` to
  # keep its path private (404). Rebuilt below with `site-url` matching
  # `docsPath`, so the package's own `sitePath` is irrelevant.
  docs ? null,
  # URL prefix the handbook is mounted at (a trailing slash is tolerated).
  docsPath ? "/docs",
  name ? "hivegent",
  tag ? "latest",
  # Plain-HTTP placeholder; front with a TLS terminator for public deployments.
  defaultSiteAddress ? ":8080",
  # Rootless HTTP port (>1024 so a non-root Caddy can bind it).
  httpPort ? 8080,
  # Backend bind port; loopback-only, never exposed — Caddy is the only ingress.
  backendPort ? 8000,
  # Loopback-only Smokescreen port used by untrusted outbound HTTP clients.
  egressProxyPort ? smokescreen.passthru.defaultPort,
  # Whether Caddy proxies `/mcp` (off → 404); the backend must also enable MCP.
  enableMcp ? false,
  # Run the chat bridge (Vercel Chat SDK) as a third supervised service and route
  # `/api/webhooks/*` to it. Requires the `bridge` package; its config comes from
  # a mounted `${dataDir}/bridge-config.json` plus `OIDC_*`/`TEAMS_*` env vars.
  enableBridge ? false,
  bridge ? null,
  bridgeHost ? "127.0.0.1",
  bridgePort ? 3001,
  # Volume holding the workspace, store, model caches, and Caddy state.
  dataDir ? "/data",
}:
let
  # `docsPath` is the single source of truth: the vhost routes on the slash-less
  # `docsPrefix`, while the book's `site-url` and the SPA's link share the
  # slash-terminated `docsUrl` (blank when no handbook is bundled, hiding the link).
  docsPrefix = lib.removeSuffix "/" docsPath;
  docsUrl = lib.optionalString (docs != null) "${docsPrefix}/";
  handbook = if docs == null then null else docs.override { sitePath = docsUrl; };
  spa = frontend.override { inherit docsUrl; };
  egressProxy = smokescreen.onLoopback egressProxyPort;

  # Docker healthcheck durations are nanoseconds.
  seconds = n: n * 1000000000;

  # dinit env-files share the plain `KEY=VALUE` format of its `--env-file`.
  toEnvFile = fileName: env: writeText fileName (lib.generators.toKeyValue { } env);

  # Render a dinit service description from an attrset. dinit treats `=` and `:`
  # identically; by convention scalars use `name = value` (override properties)
  # while additive ones (dependencies, `after`, `options`) repeat as `name: value`
  # lines — which is exactly what list-valued attrs expand to here.
  toService = lib.generators.toKeyValue {
    mkKeyValue =
      name: value:
      if lib.isList value then
        lib.concatMapStringsSep "\n" (v: "${name}: ${v}") value
      else
        "${name} = ${toString value}";
  };

  caddyfile = writeText "Caddyfile" ''
    {
      admin off
      persist_config off
      auto_https off
      http_port ${toString httpPort}
    }

    {$HIVEGENT_SITE_ADDRESS:${defaultSiteAddress}} {
      ${import ./vhost.nix {
        inherit lib enableMcp;
        frontend = spa;
        docs = handbook;
        docsPath = docsPrefix;
        upstream = "127.0.0.1:${toString backendPort}";
        bridgeUpstream = if enableBridge then "${bridgeHost}:${toString bridgePort}" else null;
      }}
    }
  '';

  # HIVEGENT_CONFIG_FILE is an absolute path so it resolves regardless of cwd;
  # bind-mount `${dataDir}/config.toml` to set it (missing file = empty config).
  backendEnv = toEnvFile "backend.env" {
    HOME = dataDir;
    HF_HOME = "${dataDir}/huggingface";
    PYTHONUNBUFFERED = "1";
    HIVEGENT_DATA_DIR = dataDir;
    HIVEGENT_CONFIG_FILE = "${dataDir}/config.toml";
    HIVEGENT_SECURITY__EGRESS_PROXY_URL = egressProxy.url;
    SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
  };

  caddyEnv = toEnvFile "caddy.env" {
    HOME = "${dataDir}/caddy";
    XDG_DATA_HOME = "${dataDir}/caddy";
  };

  # Non-secret bridge env; secrets (POSTGRES_URL, OIDC_*, TEAMS_*) come from the
  # mounted `${dataDir}/bridge-config.json` or operator-set env vars (env wins).
  bridgeEnv = toEnvFile "bridge.env" {
    HOME = dataDir;
    NODE_ENV = "production";
    HOST = bridgeHost;
    PORT = toString bridgePort;
    HIVEGENT_URL = "http://127.0.0.1:${toString backendPort}";
    BRIDGE_CONFIG_FILE = "${dataDir}/bridge-config.json";
    SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
  };

  # Shared config for both supervised daemons. Restart policy and start timeout
  # mirror the systemd unit (RestartSec/StartLimit*/TimeoutStartSec).
  # `shares-console` routes stdout/stderr to dinit's (the Docker log stream);
  # dinit's default `log-type = none` would discard it.
  daemon = {
    type = "process";
    working-dir = dataDir;
    restart = "on-failure";
    restart-delay = 5;
    restart-limit-count = 5;
    restart-limit-interval = 600;
    options = [ "shares-console" ];
  };

  services = {
    # Default target dinit brings up: pulls in every daemon.
    boot = {
      type = "internal";
      depends-on = [
        "backend"
        "caddy"
      ]
      ++ lib.optional enableBridge "bridge";
    };
    backend = daemon // {
      command = "${lib.getExe' backend "hivegent"} serve --host 127.0.0.1 --port ${toString backendPort}";
      env-file = backendEnv;
      depends-on = [ "egress-proxy" ];
      start-timeout = 600;
      # Must exceed uvicorn's `timeout_graceful_shutdown` (30s); dinit's default is 10s.
      stop-timeout = 45;
    };
    egress-proxy = daemon // {
      command = lib.getExe egressProxy.package;
    };
    caddy = daemon // {
      command = "${lib.getExe caddy} run --config ${caddyfile} --adapter caddyfile";
      env-file = caddyEnv;
      # `after` (not `depends-on`) orders Caddy behind the backend without
      # coupling lifecycles, so a backend restart within its limit does not
      # bounce Caddy.
      after = [ "backend" ];
    };
  }
  // lib.optionalAttrs enableBridge {
    bridge = daemon // {
      command = lib.getExe bridge;
      env-file = bridgeEnv;
      after = [ "backend" ];
    };
  };

  serviceDir = linkFarm "hivegent-dinit.d" (
    lib.mapAttrs (svcName: attrs: writeText svcName (toService attrs)) services
  );
in
dockerTools.streamLayeredImage {
  inherit name tag;
  created = "now";
  # Packages needing real paths rather than closure references: CA trust store
  # (/etc/ssl + /etc/pki), /etc/passwd + /tmp (fakeNss), zoneinfo. Everything
  # executed is referenced by absolute store path, so no PATH (and no `Env`) is set.
  contents = [
    tzdata
    dockerTools.caCertificates
    dockerTools.fakeNss
  ];
  # dinit reads descriptions read-only from the store (no writable scan dir). The
  # only writable state is the control socket under /run; /tmp (world-writable)
  # backs libreoffice/docling and Caddy scratch files. `chown` needs the fakeroot
  # of `fakeRootCommands` (plain `extraCommands` runs without it) so the /data
  # volume initialises owned by the runtime user.
  fakeRootCommands = ''
    mkdir -p .${dataDir}/caddy ./tmp ./run
    chmod 1777 ./tmp
    chown -R 65534:65534 .${dataDir} ./run
  '';
  config = {
    User = "65534:65534";
    # `--container` keeps dinit out of system-manager mode (exit, don't run
    # `shutdown`). Internal paths live here, not in Cmd, so a stray `docker run`
    # arg can't replace them; with no service named, dinit starts `boot`.
    Entrypoint = [
      (lib.getExe' dinit "dinit")
      "--container"
      "--services-dir"
      "${serviceDir}"
      "--socket-path"
      "/run/dinitctl"
    ];
    WorkingDir = dataDir;
    ExposedPorts."${toString httpPort}/tcp" = { };
    Volumes.${dataDir} = { };
    Healthcheck = {
      Test = [
        "CMD"
        (lib.getExe curlMinimal)
        "-fsS"
        "http://localhost:${toString httpPort}/api/health"
      ];
      Interval = seconds 30;
      Timeout = seconds 5;
      # Generous: first start loads embedding/document models.
      StartPeriod = seconds 60;
      Retries = 5;
    };
  };
  meta = {
    description = "Hivegent container with inbound and outbound proxies";
    maintainers = with lib.maintainers; [ mirkolenz ];
    platforms = lib.platforms.linux;
  };
}
