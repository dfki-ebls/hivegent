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
    let
      caddy = config.services.hivegent.caddy;
      # Slash-terminated handbook URL feeding both the book's `site-url` and the
      # SPA's link, tracked from `caddy.docsPath`.
      docsSite = "${caddy.docsPath}/";
    in
    {
      imports = [ ./nixos ];
      services.hivegent.package = lib.mkDefault perSystem.config.packages.backend;
      services.hivegent.bridge.package = lib.mkDefault perSystem.config.packages.bridge;
      # Blank the URL when `caddy.docs` is null so the SPA hides the link.
      services.hivegent.caddy.frontend = lib.mkDefault (
        perSystem.config.packages.frontend.override {
          docsUrl = lib.optionalString (caddy.docs != null) docsSite;
        }
      );
      services.hivegent.caddy.docs = lib.mkDefault (
        perSystem.config.packages.docs.override { sitePath = docsSite; }
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
        inherit (config.packages) backend frontend bridge;
        inherit (config.packages.backend.passthru.tests) pytest;
      };
      packages = {
        backend = pkgs.callPackage ../backend {
          inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
          inherit (config.packages) tessdata;
        };
        frontend = pkgs.callPackage ../frontend { };
        bridge = pkgs.callPackage ../bridge { };
        docs = pkgs.callPackage ../docs { };
        tessdata = pkgs.callPackage ./tessdata.nix { };
        release-env = pkgs.buildEnv {
          name = "release-env";
          paths = with pkgs; [
            nodejs
            python313
            uv
          ];
        };
      }
      // lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
        # All-in-one container (backend + Caddy proxy/SPA under dinit); see
        # `docker.nix`. The database stays external (the upstream `pgvector`
        # image, wired up in `compose.yaml`). Linux-only: the image embeds a
        # Linux closure, so build it on a Linux host or remote builder
        # (`nix build .#packages.x86_64-linux.docker`).
        # `bridge` is threaded in but off by default; build the bridge-enabled
        # variant with `docker.override { enableBridge = true; }`.
        docker = pkgs.callPackage ./docker.nix {
          inherit (config.packages)
            backend
            frontend
            docs
            bridge
            ;
        };
      };
    };
}
