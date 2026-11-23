{
  lib,
  pkgs,
  config,
  ...
}:
let
  cfg = config.services.hivegent;
  caddyCfg = cfg.caddy;

  # An origin the CSP may name, without a path, credentials, or a separator.
  originPattern = "https?://([A-Za-z0-9.-]+|\\[[0-9A-Fa-f:.]+])(:[0-9]+)?";
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
        of the hivegent flake. The SPA reads its OIDC client and handbook link
        at runtime from the backend's `/api/v1/config`, so changing them needs
        no rebuild.

        Set to `null` to serve the API only, without the SPA, useful when a
        deployment consumes the REST API directly. Non-API paths then return
        404. (The flake's default module wires this to the bundled `frontend`
        via `mkDefault`, so `services.hivegent.caddy.frontend = null` overrides
        it.)
      '';
    };

    docs = lib.mkPackageOption pkgs "docs" {
      nullable = true;
      default = null;
      extraDescription = ''
        Built mdbook handbook served as static files under `docsPath`. The
        flake's default module wires this to the bundled `docs` package built
        with `site-url` matching `docsPath`, so links resolve on the subpath,
        and the backend's `settings.docs_url` defaults to it, which shows the
        SPA's "Documentation" link.

        Set to `null` to keep `''${docsPath}` private (404), exposing only
        the API and SPA.
      '';
    };

    docsPath = lib.mkOption {
      type = lib.types.str;
      default = "/docs";
      description = ''
        URL prefix the handbook is mounted at, starting with a slash and
        ending without one.
        The bundled `docs` default is rebuilt with a matching `site-url`, so
        relocating the handbook only takes setting this option.
      '';
    };

    idpOrigins = lib.mkOption {
      type = lib.types.nonEmptyListOf (lib.types.strMatching originPattern);
      example = [ "https://auth.example.com" ];
      description = ''
        Origins of the OIDC provider, which the SPA's Content-Security-Policy
        lets it fetch from and frame for the sign-in, usually the origin of
        the issuer plus any further origin the provider signs in on.
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
        is intentionally not part of the reusable module, geoblocking,
        client-IP allow-lists, scanner honeypots. A blocking `handle` here
        takes precedence over the bundled handlers.
      '';
    };
  };

  config = lib.mkIf (cfg.enable && caddyCfg.enable) {
    services.hivegent.settings.docs_url = lib.mkIf (caddyCfg.docs != null) (
      lib.mkDefault "${caddyCfg.docsPath}/"
    );

    services.caddy = {
      enable = lib.mkDefault true;
      virtualHosts.hivegent = {
        hostName = caddyCfg.hostName;
        extraConfig = import ../vhost.nix {
          inherit lib;
          inherit (caddyCfg)
            frontend
            docs
            docsPath
            extraConfig
            idpOrigins
            ;
          enableHsts = caddyCfg.hsts;
          upstream = "${cfg.host}:${toString cfg.port}";
          bridgeUpstream =
            if cfg.bridge.enable then "${cfg.bridge.host}:${toString cfg.bridge.port}" else null;
          inherit (cfg.limits) maxFileSizeBytes maxCollectionSizeBytes;
        };
      };
    };
  };
}
