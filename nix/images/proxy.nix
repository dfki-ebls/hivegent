# Proxy/frontend container: Caddy serving the SPA and reverse-proxying the API,
# streamed as a layered image. Secure by default — runs as the unprivileged
# `nobody` uid on rootless high ports, ships no shell, and carries a baked
# healthcheck. The site address and backend upstream stay runtime-configurable
# through Caddy env placeholders (so one image serves any deployment); the OIDC
# provider is the backend's concern (served to the SPA at `/api/config`), so this
# image is provider-agnostic with nothing to bake in. Build-time choices — image
# name/tag, ports, volume paths, and the `frontend` bundle — are arguments
# (`docker-proxy.override { … }`). Structure follows the upstream caddy-docker
# image (Entrypoint/Cmd split, XDG file locations).
{
  lib,
  dockerTools,
  writeText,
  cacert,
  tzdata,
  curlMinimal,
  caddy,
  frontend,
  name ? "hivegent-proxy",
  tag ? "latest",
  # Defaults for the runtime env placeholders below. `:8080` serves plain HTTP;
  # set a bare domain at runtime for automatic HTTPS.
  defaultSiteAddress ? ":8080",
  defaultBackendUpstream ? "backend:8000",
  # Rootless listen ports (>1024 so a non-root Caddy can bind them). A
  # hostname-based site address uses these for auto-HTTPS; map 80/443 to them
  # externally when fronting the public internet.
  httpPort ? 8080,
  httpsPort ? 8443,
  # Caddy data (ACME state) and config mount points.
  dataDir ? "/data",
  configDir ? "/config",
}:
let
  seconds = n: n * 1000000000;

  caddyfile = writeText "Caddyfile" ''
    {
      admin off
      persist_config off
      http_port ${toString httpPort}
      https_port ${toString httpsPort}
    }

    {$HIVEGENT_SITE_ADDRESS:${defaultSiteAddress}} {
      ${import ../vhost.nix {
        inherit lib frontend;
        upstream = "{$HIVEGENT_BACKEND_UPSTREAM:${defaultBackendUpstream}}";
        mcp = false;
        hsts = false;
      }}
    }
  '';
in
dockerTools.streamLayeredImage {
  inherit name tag;
  created = "now";
  contents = [
    caddy
    frontend
    cacert
    tzdata
    curlMinimal
    dockerTools.fakeNss
  ];
  # Writable runtime dirs owned by the unprivileged user, plus a world-writable
  # /tmp for Caddy's scratch files.
  fakeRootCommands = ''
    mkdir -p .${dataDir} .${configDir} ./srv ./tmp
    chmod 1777 ./tmp
    chown -R 65534:65534 .${dataDir} .${configDir} ./srv
  '';
  config = {
    User = "65534:65534";
    Entrypoint = [ (lib.getExe caddy) ];
    Cmd = [
      "run"
      "--config"
      "${caddyfile}"
      "--adapter"
      "caddyfile"
    ];
    # https://caddyserver.com/docs/conventions#file-locations
    Env = [
      "HOME=${dataDir}"
      "XDG_DATA_HOME=${dataDir}"
      "XDG_CONFIG_HOME=${configDir}"
    ];
    WorkingDir = "/srv";
    ExposedPorts = {
      "${toString httpPort}/tcp" = { };
      "${toString httpsPort}/tcp" = { };
      "${toString httpsPort}/udp" = { };
    };
    Volumes = {
      ${dataDir} = { };
      ${configDir} = { };
    };
    Healthcheck = {
      Test = [
        "CMD"
        (lib.getExe curlMinimal)
        "-fsS"
        "http://localhost:${toString httpPort}/"
      ];
      Interval = seconds 30;
      Timeout = seconds 5;
      StartPeriod = seconds 5;
      Retries = 3;
    };
  };
  meta = {
    description = "Hivegent proxy/frontend (Caddy) container";
    maintainers = with lib.maintainers; [ mirkolenz ];
    platforms = lib.platforms.linux;
  };
}
