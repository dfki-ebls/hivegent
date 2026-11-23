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
  # It passes only the arguments the function names, so keep `{ config }`.
  flake.nixosModules.default = moduleWithSystem (
    perSystem@{ config }:
    { lib, config, ... }:
    let
      # The book's `site-url`, tracked from `caddy.docsPath`.
      docsSite = "${config.services.hivegent.caddy.docsPath}/";
    in
    {
      imports = [ ./nixos ];
      services.hivegent = {
        package = lib.mkDefault perSystem.config.packages.backend;
        bridge.package = lib.mkDefault perSystem.config.packages.bridge;
        egressProxy.package = lib.mkDefault perSystem.config.packages.smokescreen;
        caddy.frontend = lib.mkDefault perSystem.config.packages.frontend;
        caddy.docs = lib.mkDefault (perSystem.config.packages.docs.override { sitePath = docsSite; });
      };
    }
  );

  # Independent instances on one host, each in a container running the module above.
  flake.nixosModules.instances = import ./nixos/instances.nix inputs.self.nixosModules.default;

  perSystem =
    {
      lib,
      pkgs,
      config,
      inputs',
      ...
    }:
    let
      mkSbom = pkgs.callPackage ./sbom.nix {
        # bombon's own entry point, not its flake `lib`, which would evaluate a
        # second nixpkgs instead of the one everything else here is built from.
        bombon = import inputs.bombon { inherit pkgs; };
        inherit (inputs) cyclonedx-spec;
      };
    in
    {
      devShells.default = pkgs.callPackage ./shell.nix {
        treefmt = config.treefmt.build.wrapper;
        inherit (config.packages) hivegent backend;
      };
      # Everything the flake builds is checked, derived from `packages` rather
      # than listed beside it so the two cannot drift: a package added below is
      # built by CI without anyone remembering to name it here.  Outputs CI must
      # not build go to `legacyPackages` instead.
      checks = config.packages // {
        inherit (config.packages.backend.passthru.tests) pytest;
      };
      legacyPackages = {
        # Pushes and signs the multi-platform image index, with each platform's
        # SBOM attested to its image.  It spans systems, so it is no package.
        docker-manifest = inputs'.flocken.legacyPackages.mkDockerManifest {
          github.enable = true;
          # Every system's image, each carrying its SBOM as `passthru.sbom`.
          images = lib.catAttrs "docker" (lib.attrValues inputs.self.packages);
          # `latest` and the version tags cover the release branch.
          autoTags.branch = false;
          cosign.enable = true;
        };
      };
      packages = {
        backend = pkgs.callPackage ../backend {
          inherit (inputs) uv2nix pyproject-nix pyproject-build-systems;
          inherit (config.packages) tessdata;
        };
        frontend = pkgs.callPackage ../frontend { };
        bridge = pkgs.callPackage ../bridge { };
        docs = pkgs.callPackage ../docs {
          inherit ((lib.importTOML ../backend/pyproject.toml).project) version;
        };
        smokescreen = pkgs.callPackage ./smokescreen.nix { };
        tessdata = pkgs.callPackage ./tessdata.nix { };
        # The release's SBOM, which describes the app, while each image carries
        # its own as `passthru.sbom`.
        sbom = mkSbom {
          inherit (config.packages)
            backend
            frontend
            bridge
            smokescreen
            docs
            ;
        };
        # The handbook as GitHub Pages serves it, under the repository's name.
        docs-pages = config.packages.docs.override { sitePath = "/hivegent/"; };
        release-env = pkgs.buildEnv {
          name = "release-env";
          paths = [
            pkgs.nodejs
            config.packages.backend.python
            pkgs.uv
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
        # The SBOM is attached here, where `.override` re-applies it, so a
        # variant such as the bridge-enabled one describes what it contains.
        docker =
          (pkgs.callPackage ./docker.nix {
            inherit (config.packages)
              backend
              frontend
              docs
              bridge
              smokescreen
              ;
          }).overrideAttrs
            (
              finalAttrs: prev: {
                passthru = prev.passthru // {
                  sbom = "${
                    mkSbom (prev.passthru.components // { image = finalAttrs.finalPackage; })
                  }/hivegent.cdx.json";
                };
              }
            );
      };
    };
}
