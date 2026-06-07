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
          };
          frontend.command = ''
            exec ${lib.getExe' pkgs.nodejs "npm"} \
              --prefix frontend \
              run dev
          '';
        };
      };
    };
}
