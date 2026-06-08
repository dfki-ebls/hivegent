{ inputs, ... }:
{
  systems = import inputs.systems;

  imports = [
    ./processes.nix
    ./treefmt.nix
  ];

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
        };
        frontend = pkgs.callPackage ../frontend { };
      };
    };
}
