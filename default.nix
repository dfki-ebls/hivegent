{
  inputs,
  lib,
  ...
}:
{
  imports = [
    inputs.treefmt-nix.flakeModule
    inputs.process-compose.flakeModule
  ];
  systems = import inputs.systems;

  # flakenixosModules.default = ./nixos;

  perSystem =
    {
      pkgs,
      config,
      ...
    }:
    {
      devShells.default = pkgs.callPackage ./shell.nix {
        treefmt = config.treefmt.build.wrapper;
        inherit (config.packages) watch-dev backend;
      };
      checks = {
        inherit (config.packages) backend frontend;
      };
      packages = {
        backend = pkgs.callPackage ./backend {
          inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
        };
        frontend = pkgs.callPackage ./frontend { };
      };
      process-compose.watch-dev = {
        settings.processes = {
          backend.command = ''
            exec ${lib.getExe pkgs.uv} \
              --directory backend \
              run hivegent serve --host 127.0.0.1 --reload
          '';
          frontend.command = ''
            exec ${lib.getExe' pkgs.nodejs "npm"} \
              --prefix frontend \
              run dev
          '';
        };
      };
      treefmt = {
        projectRootFile = "flake.nix";
        programs = {
          nixfmt.enable = true;
          oxfmt.enable = true;
          ruff-check.enable = true;
          ruff-format.enable = true;
        };
      };
    };
}
