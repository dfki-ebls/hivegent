# Native NixOS deployment of Hivegent: the systemd backend service plus an
# opt-in Caddy vhost serving the SPA and reverse-proxying the API. Exposed as
# `nixosModules.default`; the flake fills in the package defaults per platform.
{
  imports = [
    ./service.nix
    ./caddy.nix
    ./bridge.nix
  ];
}
