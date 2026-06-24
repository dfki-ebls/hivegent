# All-in-one container: the FastAPI backend and the Caddy proxy/SPA in a single
# layered image, supervised by s6 (`s6-svscan` as PID 1). Secure by default —
# runs as the unprivileged `nobody` uid, ships no shell, needs no `--privileged`,
# and carries a baked healthcheck that exercises the whole chain (Caddy -> API).
# Caddy runs as a plain-HTTP reverse proxy here — front the container with a
# TLS-terminating proxy for public deployments (the site address stays
# runtime-configurable via a Caddy env placeholder). The backend listens on the
# loopback only, so just the HTTP port is exposed. Build-time choices — image
# name/tag, ports, the data path, and the `backend` and `frontend` bundles — are
# arguments (`docker.override { … }`). The database stays external (the upstream
# `pgvector` image, wired up in `compose.yaml`).
#
# s6 rather than systemd so the image runs unprivileged anywhere (systemd as
# PID 1 needs `--privileged`, root, and cgroup mounts). nixpkgs has no
# `s6-overlay` package, so this lays out a minimal scan directory from the
# native `s6` suite directly.
{
  lib,
  dockerTools,
  writeText,
  writeScript,
  tzdata,
  curlMinimal,
  caddy,
  s6,
  execline,
  backend,
  frontend,
  name ? "hivegent",
  tag ? "latest",
  # Default for the runtime site-address placeholder. Plain HTTP only; front the
  # container with a TLS-terminating proxy for public deployments.
  defaultSiteAddress ? ":8080",
  # Rootless HTTP port (>1024 so a non-root Caddy can bind it). Map it behind the
  # upstream terminator as needed.
  httpPort ? 8080,
  # Backend bind port. Loopback-only, never exposed — Caddy is the only ingress.
  backendPort ? 8000,
  # Whether Caddy proxies the backend's `/mcp` endpoint instead of answering it
  # with 404. Off by default; the backend must also have MCP enabled.
  enableMcp ? false,
  # Volume mount point holding the workspace, store, model caches, and Caddy's
  # state under `caddy/`.
  dataDir ? "/data",
}:
let
  # Docker healthcheck durations are nanoseconds; spell them as seconds.
  seconds = n: n * 1000000000;

  execlineb = lib.getExe' execline "execlineb";

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
        upstream = "127.0.0.1:${toString backendPort}";
      }}
    }
  '';

  # One s6 service per process. Each `run` sets only its own environment and then
  # execs the daemon, so s6-supervise tracks the real PID (signals propagate,
  # no wrapping shell). execline keeps the image shell-free.
  #
  # The backend reads an optional TOML config from `HIVEGENT_CONFIG_FILE`, pinned
  # to an absolute path in the data volume (s6 runs each service from its own
  # scan-dir, so the relative default would not resolve there). Bind-mount a file
  # at `${dataDir}/config.toml` to customise it; a missing file is simply empty,
  # and `HIVEGENT_*` env vars still override individual keys.
  caddyRun = writeScript "caddy-run" ''
    #!${execlineb} -P
    export HOME ${dataDir}/caddy
    export XDG_DATA_HOME ${dataDir}/caddy
    ${lib.getExe caddy} run --config ${caddyfile} --adapter caddyfile
  '';

  backendRun = writeScript "backend-run" ''
    #!${execlineb} -P
    export HOME ${dataDir}
    export HF_HOME ${dataDir}/huggingface
    export PYTHONUNBUFFERED 1
    export HIVEGENT_DATA_DIR ${dataDir}
    export HIVEGENT_CONFIG_FILE ${dataDir}/config.toml
    export SSL_CERT_FILE /etc/ssl/certs/ca-certificates.crt
    ${lib.getExe' backend "hivegent"} serve --host 127.0.0.1 --port ${toString backendPort}
  '';

  # No SIGTERM handler service needed: s6-svscan's default action on SIGTERM
  # (what `docker stop` sends to PID 1) already stops every service, reaps the
  # whole tree, and exits 0 — a clean shutdown out of the box.
in
dockerTools.streamLayeredImage {
  inherit name tag;
  created = "now";
  # `contents` only needs the packages that must exist as real paths: /bin for
  # the PATH-resolved helpers (s6-supervise, execline's `export`), the CA trust
  # store at every canonical location (caCertificates -> /etc/ssl + /etc/pki),
  # /etc/passwd + /tmp (fakeNss), the healthcheck curl, and zoneinfo for
  # timestamps. The backend, Caddy, and frontend ship in the closure
  # transitively (referenced by absolute store path), so they need no entry.
  contents = [
    s6
    execline
    tzdata
    dockerTools.caCertificates
    dockerTools.fakeNss
  ];
  # At runtime s6-svscan creates a `.s6-svscan/` control dir in the scan dir and
  # s6-supervise a `supervise/` dir inside each service dir, so the scan dir must
  # live on a writable filesystem, not the read-only store: lay it out under /run
  # owned by the unprivileged runtime user. /tmp backs libreoffice/docling and
  # Caddy scratch files (mktemp) and must be world-writable.
  fakeRootCommands = ''
    mkdir -p .${dataDir}/caddy ./tmp ./run/service/caddy ./run/service/backend
    chmod 1777 ./tmp
    cp ${caddyRun} ./run/service/caddy/run
    cp ${backendRun} ./run/service/backend/run
    chmod -R u+w ./run/service
    chown -R 65534:65534 .${dataDir} ./run/service
  '';
  config = {
    # Drop root: run as the `nobody` uid/gid provided by fakeNss.
    User = "65534:65534";
    # Fixed supervision command: the scan dir is an internal path, not a
    # user-overridable argument, so it belongs in the entrypoint rather than Cmd
    # (where a stray `docker run … <arg>` would silently replace it).
    Entrypoint = [
      (lib.getExe' s6 "s6-svscan")
      "/run/service"
    ];
    # s6-svscan execs s6-supervise, and execline scripts exec their helper
    # programs (`export`), both via PATH.
    Env = [ "PATH=/bin" ];
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
