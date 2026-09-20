{ inputs, lib, ... }:
{
  imports = [ inputs.process-compose.flakeModule ];

  perSystem =
    { pkgs, config, ... }:
    let
      smokescreen = config.packages.smokescreen;
      egressProxy = smokescreen.onLoopback smokescreen.defaultPort;
    in
    {
      process-compose.hivegent = {
        imports = [
          inputs.services-flake.processComposeModules.default
        ];
        services.postgres.db = {
          enable = true;
          package = pkgs.postgresql_18;
          extensions = ext: [ ext.pgvector ];
          initialDatabases = [ { name = "hivegent"; } ];
          listen_addresses = "";
          socketDir = "data/db";
        };
        settings.processes = {
          backend = {
            depends_on.db.condition = "process_healthy";
            depends_on.egress-proxy.condition = "process_started";
            command = ''
              exec ${lib.getExe pkgs.uv} \
                --project backend \
                run hivegent serve --host 127.0.0.1 --reload
            '';
            # The dev stack owns the proxy port, so it also states the URL
            # rather than relying on the backend's compiled-in default.
            environment = [ "HIVEGENT_SECURITY__EGRESS_PROXY_URL=${egressProxy.url}" ];
            # FastAPI only serves once lifespan startup (migrations, reconcile)
            # finishes, so a healthy probe means the backend is ready for traffic.
            # Cadence mirrors the NixOS deployment's `/api/health` check (Caddy's
            # `health_interval 10s` / `health_timeout 3s` in `nix/vhost.nix`), and
            # the 60 * 10s failure window matches the unit's `TimeoutStartSec = 600`
            # (`nix/nixos/service.nix`) — keeping dev in line with prod also stops
            # the probe from flooding the logs with a request every second.
            readiness_probe = {
              http_get = {
                host = "127.0.0.1";
                port = 8000;
                path = "/api/health";
              };
              initial_delay_seconds = 1;
              period_seconds = 10;
              timeout_seconds = 3;
              failure_threshold = 60;
            };
          };
          egress-proxy.command = "exec ${lib.getExe egressProxy.package}";
          # Gate the dev server on a ready backend so the SPA's startup fetches
          # never hit a booting backend and flood the proxy with ECONNREFUSED.
          frontend = {
            depends_on.backend.condition = "process_healthy";
            command = ''
              exec ${lib.getExe' pkgs.nodejs "npm"} \
                --prefix frontend \
                run dev
            '';
          };
        };
      };
    };
}
