# Shared `credentials` option of the Hivegent units. Each systemd credential is
# named like the environment variable it replaces and read by the service from
# `$CREDENTIALS_DIRECTORY`, so secrets never enter the Nix store or the unit's
# environment, and only the unit's (dynamic) user can read them.
lib: {
  option =
    example:
    lib.mkOption {
      type = lib.types.attrsOf (
        lib.types.pathWith {
          inStore = false;
          absolute = true;
        }
      );
      default = { };
      example = lib.literalExpression example;
      description = ''
        Secrets passed via `LoadCredential=`, mapping the name of the
        environment variable a secret replaces to a root-readable file, e.g.
        a sops-nix or agenix secret. systemd copies the file into the unit's
        private credential directory at start, so a missing file fails the
        start instead of running without the secret.
      '';
    };

  load = lib.mapAttrsToList (name: path: "${name}:${path}");
}
