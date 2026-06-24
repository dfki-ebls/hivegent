{ inputs, lib, ... }:
{
  imports = [ inputs.process-compose.flakeModule ];

  perSystem =
    { pkgs, ... }:
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
            command = ''
              exec ${lib.getExe pkgs.uv} \
                --project backend \
                run hivegent serve --host 127.0.0.1 --reload
            '';
            # FastAPI only serves once lifespan startup (migrations, reconcile)
            # finishes, so a healthy probe means the backend is ready for traffic.
            readiness_probe = {
              http_get = {
                host = "127.0.0.1";
                port = 8000;
                path = "/api/health";
              };
              initial_delay_seconds = 1;
              period_seconds = 1;
              timeout_seconds = 3;
              failure_threshold = 60;
            };
          };
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
