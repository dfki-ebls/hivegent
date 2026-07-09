# Native NixOS deployment of the Hivegent chat bridge: a hardened systemd unit
# running the Vercel Chat SDK service that relays between hivegent and external
# chat platforms (Teams, Slack, ...). Optional and independent of the backend
# unit; both share the Postgres server but not each other's schema.
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
      default = 3001;
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
          hivegentUrl = "http://127.0.0.1:8000";
          oidc = {
            issuer = "https://auth.example.com";
            clientId = "hivegent-bot";
          };
          botUserName = "hivegent";
          adapters.teams = true;
        }
      '';
      description = ''
        Non-secret bridge configuration rendered to JSON in the Nix store and
        passed via `BRIDGE_CONFIG_FILE`. Environment variables (see
        `environment`/`environmentFile`) override individual keys.

        Do NOT put secrets here — anything in this attrset lands in
        `/nix/store`. Keep `oidc.clientSecret`, `POSTGRES_URL`, and the
        `TEAMS_APP_*` credentials in `environmentFile`.
      '';
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      description = ''
        Plain environment variables set on the unit — non-secret overrides of
        settings keys (e.g. `ENABLE_TEAMS`) or unrelated vars.
      '';
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/etc/hivegent/bridge.env";
      description = ''
        File in `KEY=VALUE` format forwarded via `EnvironmentFile`. Use it for
        secrets — `OIDC_CLIENT_SECRET`, `POSTGRES_URL`, `TEAMS_APP_ID`,
        `TEAMS_APP_PASSWORD`, `TEAMS_APP_TENANT_ID` — so they never land in the
        Nix store. Missing files are tolerated (systemd's `-` prefix).
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.hivegent-bridge = {
      description = "Hivegent chat bridge";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];

      environment = cfg.environment // {
        HOME = "/var/lib/hivegent-bridge";
        NODE_ENV = "production";
        HOST = cfg.host;
        PORT = toString cfg.port;
        BRIDGE_CONFIG_FILE = "${configFile}";
      };

      serviceConfig = hardening // {
        Type = "exec";
        Restart = "on-failure";
        RestartSec = 5;
        # Must exceed the graceful shutdown window before SIGKILL.
        TimeoutStopSec = 15;

        DynamicUser = true;
        StateDirectory = "hivegent-bridge";
        WorkingDirectory = "/var/lib/hivegent-bridge";

        EnvironmentFile = lib.optional (cfg.environmentFile != null) "-${cfg.environmentFile}";

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
