{
  inputs,
  self,
  lib',
  lib,
  ...
}:
{
  imports = [
    inputs.treefmt-nix.flakeModule
    inputs.process-compose.flakeModule
  ];
  systems = import inputs.systems;

  flake = {
    nixosModules.default = ./nixos;
    nixosConfigurations.default = inputs.nixpkgs-unstable.lib.nixosSystem {
      system = null;
      specialArgs = {
        inherit inputs lib';
      };
      modules = [ self.nixosModules.default ];
    };
  };

  perSystem =
    {
      pkgs,
      config,
      ...
    }:
    {
      devShells.default = pkgs.callPackage ./shell.nix {
        treefmt = config.treefmt.build.wrapper;
        inherit (config.packages) watch-dev;
      };
      checks = {
        inherit (config.packages) backend frontend;
      };
      packages = {
        backend =
          let
            inherit
              (pkgs.callPackage ./backend {
                inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
              })
              pythonSet
              workspace
              mkApplication
              ;
          in
          mkApplication {
            venv = pythonSet.mkVirtualEnv "snipscout-env" workspace.deps.optionals;
            package = pythonSet.snipscout;
          };
        frontend = pkgs.callPackage ./frontend { };
      };
      process-compose.watch-dev = {
        settings.processes = {
          backend.command = ''
            exec ${lib.getExe pkgs.uv} \
              --directory backend \
              run snipscout serve --host 127.0.0.1 --reload
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
          biome = {
            enable = true;
            validate.enable = false;
            settings = {
              formatter.indentStyle = "space";
              css.formatter.enabled = true;
              css.parser.tailwindDirectives = true;
            };
          };
          nixfmt.enable = true;
          ruff-check.enable = true;
          ruff-format.enable = true;
        };
        settings.formatter.biome.excludes = [
          "frontend/src/components/*/*.tsx"
        ];
      };
    };
}
