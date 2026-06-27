{ inputs, moduleWithSystem, ... }:
{
  systems = import inputs.systems;

  imports = [
    ./processes.nix
    ./treefmt.nix
  ];

  # Native deployment: a self-contained NixOS module whose `package` and
  # `caddy.frontend` default to this flake's builds for the host platform.
  # `moduleWithSystem` resolves `perSystem.config` against the importing
  # system, so a consumer only needs to import the module and set options.
  flake.nixosModules.default = moduleWithSystem (
    perSystem@{ config }:
    { lib, config, ... }:
    {
      imports = [ ./nixos ];
      services.hivegent.package = lib.mkDefault perSystem.config.packages.backend;
      services.hivegent.caddy.frontend = lib.mkDefault perSystem.config.packages.frontend;
      # Build the handbook with `site-url` tracking the mount point so links
      # keep working when the operator relocates it via `caddy.docsPath`.
      services.hivegent.caddy.docs = lib.mkDefault (
        perSystem.config.packages.docs.override {
          sitePath = "${config.services.hivegent.caddy.docsPath}/";
        }
      );
    }
  );

  perSystem =
    {
      lib,
      pkgs,
      config,
      ...
    }:
    {
      devShells.default = pkgs.callPackage ./shell.nix {
        treefmt = config.treefmt.build.wrapper;
        inherit (config.packages) hivegent backend;
      };
      checks = {
        inherit (config.packages) backend frontend;
        inherit (config.packages.backend.passthru.tests) pytest;
      };
      packages = {
        backend = pkgs.callPackage ../backend {
          inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
          inherit (config.packages) tessdata;
        };
        frontend = pkgs.callPackage ../frontend { };
        docs = pkgs.callPackage ../docs { };
        tessdata = pkgs.callPackage ./tessdata.nix { };
      }
      // lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
        # All-in-one container (backend + Caddy proxy/SPA under dinit); see
        # `docker.nix`. The database stays external (the upstream `pgvector`
        # image, wired up in `compose.yaml`). Linux-only: the image embeds a
        # Linux closure, so build it on a Linux host or remote builder
        # (`nix build .#packages.x86_64-linux.docker`).
        docker = pkgs.callPackage ./docker.nix {
          inherit (config.packages) backend frontend docs;
        };
      };
    };
}
