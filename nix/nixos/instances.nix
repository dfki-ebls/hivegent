# Independent Hivegent instances on one host, such as prod, staging, and
# testing: each runs the unchanged Hivegent module in a NixOS container with
# its own database, data directory, credentials, and private network, and the
# host's Caddy terminates TLS for every instance and proxies to its container.
# Exposed as `nixosModules.instances`, applied to `nixosModules.default`.
hivegent:
{ lib, config, ... }:
let
  cfg = config.services.hivegent.instances;
in
{
  options.services.hivegent.instances = lib.mkOption {
    default = { };
    description = ''
      Hivegent instances by container name, at most 11 characters,
      since the name also names the container's network interface.
    '';
    type = lib.types.attrsOf (
      lib.types.submodule {
        options = {
          hostName = lib.mkOption {
            type = lib.types.str;
            example = "staging.hivegent.example.com";
            description = "Domain the host's Caddy serves the instance on, with TLS.";
          };

          hostAddress = lib.mkOption {
            type = lib.types.str;
            example = "10.233.1.1";
            description = "Address of the host on its private link to the container.";
          };

          address = lib.mkOption {
            type = lib.types.str;
            example = "10.233.1.2";
            description = "Address of the container on its private link to the host.";
          };

          config = lib.mkOption {
            type = lib.types.deferredModule;
            default = { };
            description = ''
              NixOS configuration of the container, such as its
              `services.hivegent.settings` and `caddy.idpOrigins`.
            '';
          };

          container = lib.mkOption {
            type = lib.types.attrsOf lib.types.anything;
            default = { };
            example = lib.literalExpression ''
              { bindMounts."/run/secrets/hivegent".hostPath = "/run/secrets/hivegent-staging"; }
            '';
            description = ''
              Further options of the container, such as `bindMounts` that make
              the instance's credential files visible inside it.
            '';
          };
        };
      }
    );
  };

  config = lib.mkIf (cfg != { }) {
    assertions = lib.mapAttrsToList (name: _: {
      assertion = lib.stringLength name <= 11;
      message = "The Hivegent instance name `${name}` is longer than the 11 characters a container name allows.";
    }) cfg;

    containers = lib.mapAttrs (
      _: instance:
      {
        autoStart = true;
        privateNetwork = true;
        inherit (instance) hostAddress;
        localAddress = instance.address;
        config = {
          imports = [
            hivegent
            instance.config
          ];
          services.hivegent = {
            enable = true;
            postgresql.createLocally = lib.mkDefault true;
            # Plain HTTP on any host name, since the host's Caddy terminates TLS
            # and passes the instance's domain on.
            caddy = {
              enable = true;
              hostName = ":80";
              hsts = false;
            };
          };
          services.postgresql.enable = lib.mkDefault true;
          networking.firewall.allowedTCPPorts = [ 80 ];
          system.stateVersion = lib.mkDefault config.system.stateVersion;
        };
      }
      // instance.container
    ) cfg;

    services.caddy = {
      enable = true;
      virtualHosts = lib.mapAttrs' (
        name: instance:
        lib.nameValuePair "hivegent-${name}" {
          inherit (instance) hostName;
          extraConfig = ''
            header Strict-Transport-Security "max-age=31536000; includeSubDomains"
            reverse_proxy http://${instance.address}
          '';
        }
      ) cfg;
    };
  };
}
