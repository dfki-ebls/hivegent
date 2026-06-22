{
  lib,
  pkgs,
  config,
  ...
}:
let
  cfg = config.services.hivegent;
  caddyCfg = cfg.caddy;
in
{
  options.services.hivegent.caddy = {
    enable = lib.mkEnableOption "a Caddy virtual host serving the Hivegent frontend and API";

    hostName = lib.mkOption {
      type = lib.types.str;
      example = "hivegent.example.com";
      description = ''
        Caddy site address for the vhost. A bare domain makes Caddy manage
        TLS automatically; use `:80` or `http://…` to serve plain HTTP
        behind an external terminator.
      '';
    };

    frontend = lib.mkPackageOption pkgs "frontend" {
      nullable = true;
      default = null;
      extraDescription = ''
        Built SPA served as static files. Defaults to the `frontend` package
        of the hivegent flake. The SPA reads its OIDC config at runtime from the
        backend's `/api/config`, so no per-deployment rebuild is needed.

        Set to `null` to serve the API only, without the SPA — useful when a
        deployment consumes the REST API directly. Non-API paths then return
        404. (The flake's default module wires this to the bundled `frontend`
        via `mkDefault`, so `services.hivegent.caddy.frontend = null` overrides
        it.)
      '';
    };

    hsts = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Whether to emit a `Strict-Transport-Security` header. Only effective
        when the vhost is served over TLS; turn off when terminating TLS
        elsewhere so the header is set once upstream.
      '';
    };

    extraConfig = lib.mkOption {
      type = lib.types.lines;
      default = "";
      description = ''
        Caddy directives injected at the top of the vhost, before the API and
        SPA route handlers. The place for deployment-specific hardening that
        is intentionally not part of the reusable module — geoblocking,
        client-IP allow-lists, scanner honeypots. A blocking `handle` here
        takes precedence over the bundled handlers.
      '';
    };
  };

  config = lib.mkIf (cfg.enable && caddyCfg.enable) {
    services.caddy = {
      enable = lib.mkDefault true;
      virtualHosts.hivegent = {
        hostName = caddyCfg.hostName;
        extraConfig = import ../vhost.nix {
          inherit lib;
          inherit (caddyCfg) frontend hsts extraConfig;
          upstream = "${cfg.host}:${toString cfg.port}";
          mcp = cfg.settings.mcp.enable or false;
        };
      };
    };
  };
}
