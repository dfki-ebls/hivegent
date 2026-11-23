# Native NixOS deployment of the Hivegent chat bridge: a hardened systemd unit
# running the Vercel Chat SDK service that relays between hivegent and external
# chat platforms (Teams, Slack, ...). Optional, and ordered after the backend
# unit when both run on one host, sharing its Postgres server but not its schema.
{
  lib,
  pkgs,
  config,
  ...
}:
let
  cfg = config.services.hivegent.bridge;

  jsonFormat = pkgs.formats.json { };
  settings = cfg.settings // {
    inherit (cfg) host port;
  };
  configFile = jsonFormat.generate "hivegent-bridge-config.json" settings;

  hardening = import ./hardening.nix;
  credentials = import ./credentials.nix lib "hivegent-bridge";
  backendUnit = lib.optional config.services.hivegent.enable "hivegent.service";
in
{
  options.services.hivegent.bridge = {
    enable = lib.mkEnableOption "the Hivegent chat bridge";

    package = lib.mkPackageOption pkgs "hivegent-bridge" {
      default = null;
      extraDescription = ''
        Provides `bin/hivegent-bridge`. Defaults to the `bridge` package of
        the hivegent flake for the host platform.
      '';
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = ''
        Address the bridge binds to. Stays on the loopback by default,
        external traffic should reach webhooks through a reverse proxy.
      '';
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 3003;
      description = ''
        Webhook port the bridge listens on. The rendered config JSON, Caddy
        upstream, and systemd bind allow list derive from this option, so the
        default lives here alone.
      '';
    };

    settings = lib.mkOption {
      type = jsonFormat.type;
      default = { };
      example = lib.literalExpression ''
        {
          backendUrl = "http://127.0.0.1:8000";
          oidc = {
            issuer = "https://auth.example.com";
            clientId = "hivegent-bot";
          };
          botUserName = "hivegent";
          adapters.teams = true;
          teams = {
            appId = "00000000-0000-0000-0000-000000000000";
            tenantId = "00000000-0000-0000-0000-000000000000";
            allowedTenants = [ "00000000-0000-0000-0000-000000000000" ];
          };
        }
      '';
      description = ''
        Non-secret bridge configuration rendered to JSON in the Nix store and
        passed via `BRIDGE_CONFIG_FILE`, which the bridge validates strictly.
        `backendUrl` and `adapters.teams` are required, and `host` and `port`
        come from the options of the same name.

        The Teams adapter refuses to start without `oidc` and a `teams`
        section with a nonempty `allowedTenants`, the Entra tenant ids whose
        activities it answers.

        Do NOT put secrets here, anything in this attrset lands in
        `/nix/store`. Pass `POSTGRES_URL`, `OIDC_CLIENT_SECRET`, and
        `TEAMS_APP_PASSWORD` as `credentials`.
      '';
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      description = ''
        Plain environment variables set on the unit, the bridge reading none
        but its secrets.
      '';
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/etc/hivegent/bridge.env";
      description = ''
        Shortcut for the unit's `EnvironmentFile=`, a `KEY=VALUE` file read at
        start that overrides `environment`.
        Prefer `credentials` for secrets.
      '';
    };

    credentials = credentials.option ''
      {
        POSTGRES_URL = "/run/secrets/hivegent-bridge-postgres-url";
        OIDC_CLIENT_SECRET = "/run/secrets/hivegent-bridge-oidc-client-secret";
        TEAMS_APP_PASSWORD = "/run/secrets/hivegent-bridge-teams-app-password";
      }
    '';
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.hasAttrByPath [ "adapters" "teams" ] cfg.settings;
        message = "services.hivegent.bridge.settings.adapters.teams must be set.";
      }
    ];

    systemd.services.hivegent-bridge = {
      description = "Hivegent chat bridge";
      wantedBy = [ "multi-user.target" ];
      # The bridge checks its principal with the backend at startup, retrying while it boots.
      after = [ "network.target" ] ++ backendUnit;
      wants = backendUnit;

      environment = cfg.environment // {
        HOME = "/var/lib/hivegent-bridge";
        NODE_ENV = "production";
        BRIDGE_CONFIG_FILE = "${configFile}";
      };

      serviceConfig =
        credentials.serviceConfig cfg.credentials
        // hardening
        // {
          Type = "exec";
          Restart = "on-failure";
          RestartSec = 5;
          # Must exceed the graceful shutdown window before SIGKILL.
          TimeoutStopSec = 15;

          DynamicUser = true;
          StateDirectory = "hivegent-bridge";
          WorkingDirectory = "/var/lib/hivegent-bridge";

          EnvironmentFile = lib.optional (cfg.environmentFile != null) cfg.environmentFile;

          ExecStart = lib.getExe cfg.package;

          SocketBindAllow = "tcp:${toString cfg.port}";
        };

      unitConfig = {
        StartLimitBurst = 5;
        StartLimitIntervalSec = 600;
      };
    };
  };
}
