# All-in-one container: FastAPI backend + Caddy proxy/SPA in one layered image,
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
  # Whether Caddy proxies `/mcp` (off → 404); the backend must also enable MCP.
  enableMcp ? false,
  # Volume holding the workspace, store, model caches, and Caddy state.
  dataDir ? "/data",
}:
let
  # The handbook's `site-url` must match its mount point, so derive both from
  # the normalised `docsPath` here. This makes `docsPath` the single source of
  # truth and tolerates a stray trailing slash that would otherwise double up.
  docsPrefix = lib.removeSuffix "/" docsPath;
  handbook = if docs == null then null else docs.override { sitePath = "${docsPrefix}/"; };

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
        inherit lib frontend enableMcp;
        docs = handbook;
        docsPath = docsPrefix;
        upstream = "127.0.0.1:${toString backendPort}";
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
    SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
  };

  caddyEnv = toEnvFile "caddy.env" {
    HOME = "${dataDir}/caddy";
    XDG_DATA_HOME = "${dataDir}/caddy";
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

  serviceDir = linkFarm "hivegent-dinit.d" (
    lib.mapAttrs (svcName: attrs: writeText svcName (toService attrs)) {
      # Default target dinit brings up: pulls in both daemons.
      boot = {
        type = "internal";
        depends-on = [
          "backend"
          "caddy"
        ];
      };
      backend = daemon // {
        command = "${lib.getExe' backend "hivegent"} serve --host 127.0.0.1 --port ${toString backendPort}";
        env-file = backendEnv;
        start-timeout = 600;
        # Must exceed uvicorn's `timeout_graceful_shutdown` (30s); dinit's default is 10s.
        stop-timeout = 45;
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
    description = "Hivegent all-in-one container (backend + Caddy proxy/SPA)";
    maintainers = with lib.maintainers; [ mirkolenz ];
    platforms = lib.platforms.linux;
  };
}
