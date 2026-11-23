# All-in-one container: FastAPI backend, outbound proxy, and Caddy/SPA in one image,
# supervised by dinit as PID 1. Secure by default (unprivileged `nobody` uid, only
# `/bin/sh` and `hivegent-manage` on the PATH, no `--privileged`) with a baked
# healthcheck across the chain (Caddy -> API). Caddy serves plain HTTP, front it with a TLS terminator for public use.
# Build-time choices (name/tag, ports, data path, bundles) are `docker.override`
# arguments. The database stays external (pgvector image, see `compose.yaml`).
#
# dinit not systemd so the image runs unprivileged anywhere. Its `--container`
# mode exits on SIGTERM (from `docker stop`) after stopping every service, and it
# reads service descriptions read-only from the store. The service wiring mirrors
# the NixOS systemd unit (`nixos/service.nix`).
{
  lib,
  callPackage,
  dockerTools,
  writeText,
  writeShellScript,
  runCommand,
  runtimeShell,
  runtimeShellPackage,
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
  # URL prefix the handbook is mounted at, starting with a slash and ending without one.
  docsPath ? "/docs",
  name ? "hivegent",
  tag ? "latest",
  # Plain-HTTP placeholder, front with a TLS terminator for public deployments.
  defaultSiteAddress ? ":8080",
  # Rootless HTTP port (>1024 so a non-root Caddy can bind it).
  httpPort ? 8080,
  # Backend bind port, loopback-only, never exposed. Caddy is the only ingress.
  backendPort ? 8000,
  # Loopback-only Smokescreen port used by untrusted outbound HTTP clients.
  egressProxyPort ? smokescreen.passthru.defaultPort,
  # Run the chat bridge (Vercel Chat SDK) as a third supervised service and route
  # `/api/webhooks/*` to it. Requires the `bridge` package. Its config comes from
  # a mounted `${dataDir}/bridge-config.json` plus `hivegent-bridge.*` secrets.
  enableBridge ? false,
  bridge ? null,
  bridgeHost ? "127.0.0.1",
  bridgePort ? 3003,
  # Volume holding the workspace, store, model caches, and Caddy state.
  dataDir ? "/data",
}:
let
  # `docsPath` is the single source of truth: the vhost routes on it, while the
  # book's `site-url` is slash-terminated.
  handbook = if docs == null then null else docs.override { sitePath = "${docsPath}/"; };
  egressProxy = smokescreen.onLoopback egressProxyPort;

  # Rebuilt with the container, like /tmp, which stays in its writable layer
  # rather than a tmpfs, since an upload or an extracted archive may outgrow memory.
  cacheDir = "/var/cache/hivegent";

  # Docker healthcheck durations are nanoseconds.
  seconds = n: n * 1000000000;

  # dinit env-files share the plain `KEY=VALUE` format of its `--env-file`.
  toEnvFile = fileName: env: writeText fileName (lib.generators.toKeyValue { } env);

  # Render a dinit service description from an attrset. dinit treats `=` and `:`
  # identically. By convention scalars use `name = value` (override properties)
  # while additive ones (dependencies, `after`, `options`) repeat as `name: value`
  # lines, which is exactly what list-valued attrs expand to here.
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

    {$CADDY_SITE_ADDRESS:${defaultSiteAddress}} {
      ${import ./vhost.nix {
        inherit lib;
        inherit frontend;
        docs = handbook;
        inherit docsPath;
        upstream = "127.0.0.1:${toString backendPort}";
        idpOrigins = [ "{$CADDY_IDP_ORIGINS}" ];
        bridgeUpstream = if enableBridge then "${bridgeHost}:${toString bridgePort}" else null;
      }}
    }
  '';

  # Docker and Podman mount secrets as files under /run/secrets, the same
  # interface systemd credentials offer, so both services read them from there.
  # Name each secret `<service>.<NAME>` after the setting it supplies, e.g.
  # `hivegent.LLM__PROVIDERS__LOCAL__API_KEY` or `hivegent-bridge.POSTGRES_URL`.
  credentialsDirectory = "/run/secrets";

  # HIVEGENT_CONFIG_FILE is an absolute path so it resolves regardless of cwd.
  # Bind-mount `${dataDir}/config.toml`, the backend refuses to start without it.
  backendEnv = toEnvFile "backend.env" {
    HOME = dataDir;
    HF_HOME = "${dataDir}/huggingface";
    # Third-party caches stay in the container, off the backed-up volume.
    XDG_CACHE_HOME = cacheDir;
    PYTHONUNBUFFERED = "1";
    HIVEGENT_DATA_DIR = dataDir;
    HIVEGENT_CONFIG_FILE = "${dataDir}/config.toml";
    HIVEGENT_SECURITY__EGRESS_PROXY_URL = egressProxy.url;
    CREDENTIALS_DIRECTORY = credentialsDirectory;
  };

  caddyEnv = toEnvFile "caddy.env" {
    HOME = "${dataDir}/caddy";
    XDG_DATA_HOME = "${dataDir}/caddy";
  };

  # Non-secret bridge env. The secrets (POSTGRES_URL, OIDC_CLIENT_SECRET, and
  # TEAMS_APP_PASSWORD) come from `hivegent-bridge.*` files in /run/secrets,
  # everything else from `${dataDir}/bridge-config.json`, whose `backendUrl` is
  # `http://127.0.0.1:${backendPort}` and whose `host` and `port` must match
  # `bridgeHost` and `bridgePort`.
  bridgeEnv = toEnvFile "bridge.env" {
    HOME = dataDir;
    NODE_ENV = "production";
    BRIDGE_CONFIG_FILE = "${dataDir}/bridge-config.json";
    CREDENTIALS_DIRECTORY = credentialsDirectory;
  };

  # Shared config for the supervised daemons. Restart policy and start timeout
  # mirror the systemd units (RestartSec/StartLimit*/TimeoutStartSec).
  # `shares-console` routes stdout/stderr to dinit's (the Docker log stream),
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
      ];
      # Soft, so a bridge that gave up stops alone instead of taking the web app down with it.
      waits-for = lib.optional enableBridge "bridge";
    };
    backend = daemon // {
      command = "${lib.getExe' backend "hivegent"} serve --host 127.0.0.1 --port ${toString backendPort}";
      env-file = backendEnv;
      depends-on = [ "egress-proxy" ];
      start-timeout = 600;
      # Must exceed uvicorn's `timeout_graceful_shutdown` (30s), dinit's default is 10s.
      stop-timeout = 45;
      # A server that lost the database connection holding its locks stops itself
      # with SIGTERM, which dinit does not count as a failure, while `dinitctl stop`
      # still keeps it down. Restarts at least 10s apart never reach the limit,
      # so the backend outlasts a database outage of any length.
      restart = "yes";
      restart-delay = 10;
      restart-limit-count = 60;
    };
    egress-proxy = daemon // {
      command = lib.getExe egressProxy.package;
    };
    caddy = daemon // {
      command = "${caddyStart}";
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

  # Caddy names the space-separated CADDY_IDP_ORIGINS in the SPA's CSP verbatim,
  # and would silently name none without them, so a Caddy whose sign-in the
  # CSP would block never starts, which fails the `boot` target.
  caddyStart = writeShellScript "hivegent-caddy" ''
    : "''${CADDY_IDP_ORIGINS:?Set CADDY_IDP_ORIGINS to the identity provider's origins, which the SPA's CSP needs for the sign-in}"

    exec ${lib.getExe caddy} run --config ${caddyfile} --adapter caddyfile
  '';

  dinitSocket = "/run/dinitctl";

  # `docker exec <container> hivegent-manage ...` runs the CLI as the backend
  # service runs, with its environment and secrets. The backend is released
  # from `boot` while stopped, so stopping it does not stop every service.
  manage = callPackage ./manage.nix { } {
    runtimeInputs = [ dinit ];
    prelude = ''
      set -a
      # shellcheck source=/dev/null
      . ${backendEnv}
      set +a

      dinitctl() {
        command dinitctl --socket-path ${dinitSocket} --quiet "$@"
      }
    '';
    run = lib.getExe' backend "hivegent";
    running = "dinitctl is-started backend";
    stop = "dinitctl rm-dep regular boot backend; dinitctl stop backend";
    start = "dinitctl start backend; dinitctl add-dep regular boot backend; dinitctl release backend";
  };

  # Subprocess runners exec `/bin/sh -c`, served by the shell already in the closure.
  binSh = runCommand "bin-sh" { } ''
    mkdir -p $out/bin
    ln -s ${runtimeShell} $out/bin/sh
  '';

  notices = callPackage ./notices.nix {
    inherit (backend) version;
    inherit (backend.meta) license;
  };
in
dockerTools.streamLayeredImage {
  inherit name tag;
  created = "now";
  # Packages needing real paths rather than closure references: CA trust store
  # (/etc/ssl + /etc/pki), /etc/passwd + /tmp (fakeNss), zoneinfo, `/bin/sh`, and
  # `/bin/hivegent-manage` for `docker exec`, found on its default PATH. Everything
  # else executed is referenced by absolute store path, so no PATH is set.
  contents = [
    tzdata
    dockerTools.caCertificates
    dockerTools.fakeNss
    binSh
    notices
    manage
  ];
  # dinit reads descriptions read-only from the store (no writable scan dir). The
  # only writable state is the control socket under /run, the backend's caches,
  # and /tmp (world-writable), which backs docling and Caddy temporary files.
  # `chown` needs the fakeroot of `fakeRootCommands` (plain `extraCommands` runs
  # without it) so the /data volume initialises owned by the runtime user.
  fakeRootCommands = ''
    mkdir -p .${dataDir}/caddy .${cacheDir} ./tmp ./run
    chmod 1777 ./tmp
    chown -R 65534:65534 .${dataDir} .${cacheDir} ./run
  '';
  config = {
    User = "65534:65534";
    # `--container` keeps dinit out of system-manager mode (exit, don't run
    # `shutdown`). Internal paths live here, not in Cmd, so a stray `docker run`
    # arg can't replace them. With no service named, dinit starts `boot`.
    Entrypoint = [
      (lib.getExe' dinit "dinit")
      "--container"
      "--services-dir"
      "${serviceDir}"
      "--socket-path"
      dinitSocket
    ];
    WorkingDir = dataDir;
    # The SPA links the handbook only when the image serves one, and since dinit
    # hands its services its own environment beneath their env-files, an
    # operator's `-e HIVEGENT_DOCS_URL=...` points the link elsewhere.
    Env = lib.optional (handbook != null) "HIVEGENT_DOCS_URL=${docsPath}/";
    # The license is Hivegent's own, as the OCI spec reads it, while the
    # third-party ones are in /usr/share/doc/hivegent/NOTICE.
    Labels = {
      "org.opencontainers.image.title" = "Hivegent";
      "org.opencontainers.image.description" = backend.meta.description;
      "org.opencontainers.image.version" = backend.version;
      "org.opencontainers.image.licenses" = backend.meta.license.spdxId;
      "org.opencontainers.image.vendor" = "DFKI";
    };
    ExposedPorts."${toString httpPort}/tcp" = { };
    Volumes.${dataDir} = { };
    Healthcheck = {
      Test = [
        "CMD"
        (lib.getExe curlMinimal)
        "-fsS"
        "http://localhost:${toString httpPort}/api/v1/health"
      ];
      Interval = seconds 30;
      Timeout = seconds 5;
      # Generous: first start loads embedding/document models.
      StartPeriod = seconds 60;
      Retries = 5;
    };
  };
  passthru = {
    # The SBOM reads the component version from here, since the image's name
    # carries none and BSI TR-03183-2 requires one.
    inherit (backend) version;
    # Packages the image runs that are referenced only by store path, which the
    # SBOM cannot reach through the derivation (`nix/sbom.nix`).
    runtimeInputs = [
      caddy
      dinit
      curlMinimal
      runtimeShellPackage
    ];
    # The packages the image ships, which its SBOM describes.
    components = {
      inherit backend smokescreen;
      inherit frontend;
      docs = handbook;
      bridge = if enableBridge then bridge else null;
    };
  };
  meta = {
    description = "Hivegent container with inbound and outbound proxies";
    maintainers = with lib.maintainers; [ mirkolenz ];
    platforms = lib.platforms.linux;
  };
}
