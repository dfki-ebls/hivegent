# Containerized deployment: `docker-backend` (the FastAPI service) and
# `docker-proxy` (Caddy serving the SPA and reverse-proxying the API), each a
# `callPackage` of its own file under `images/` so every parameter is
# overridable. The database is the upstream `pgvector` image, wired up in
# `compose.yaml`. Linux-only: the images embed Linux closures, so build them on
# a Linux host or remote builder (`nix build .#packages.x86_64-linux.docker-*`).
{ lib, ... }:
{
  perSystem =
    { pkgs, config, ... }:
    {
      packages = lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
        docker-backend = pkgs.callPackage ./images/backend.nix {
          inherit (config.packages) backend;
        };
        docker-proxy = pkgs.callPackage ./images/proxy.nix {
          inherit (config.packages) frontend;
        };
      };
    };
}
