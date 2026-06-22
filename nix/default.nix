{ inputs, moduleWithSystem, ... }:
{
  systems = import inputs.systems;

  imports = [
    ./processes.nix
    ./treefmt.nix
    ./docker.nix
  ];

  # Native deployment: a self-contained NixOS module whose `package` and
  # `caddy.frontend` default to this flake's builds for the host platform.
  # `moduleWithSystem` resolves `perSystem.config` against the importing
  # system, so a consumer only needs to import the module and set options.
  flake.nixosModules.default = moduleWithSystem (
    perSystem@{ config }:
    { lib, ... }:
    {
      imports = [ ./nixos ];
      services.hivegent.package = lib.mkDefault perSystem.config.packages.backend;
      services.hivegent.caddy.frontend = lib.mkDefault perSystem.config.packages.frontend;
    }
  );

  perSystem =
    {
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
        tessdata = pkgs.callPackage ./tessdata.nix { };
      };
    };
}
