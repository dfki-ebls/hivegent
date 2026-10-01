# Shared `credentials` option of the Hivegent units. Each systemd credential is
# named like the environment variable it replaces and read by the service from
# `$CREDENTIALS_DIRECTORY`, so secrets never enter the Nix store or the unit's
# environment, and only the unit's (dynamic) user can read them.
lib:
let
  inherit (lib) mkOption types;

  # A plain path is shorthand for `source`, so every value reaches the module
  # in one shape. Every path is coerced, as a submodule would import one as a
  # module, and `source` keeps it outside the Nix store, which every local
  # user can read.
  credential = types.coercedTo types.path (source: { inherit source; }) (
    types.submodule {
      options = {
        source = mkOption {
          type = types.nullOr types.externalPath;
          default = null;
          description = ''
            File from which systemd loads the credential. `null` imports the
            credential of the same name with `ImportCredential=` from the
            system credential store, such as `/etc/credstore` and
            `/etc/credstore.encrypted`.
          '';
        };
        encrypted = mkOption {
          type = types.bool;
          default = false;
          description = ''
            Whether to load and decrypt `source` with `LoadCredentialEncrypted=`.
            It must be encrypted under the credential's name, e.g. with
            `systemd-creds encrypt --name=<name>`.
          '';
        };
      };
    }
  );
in
{
  option =
    example:
    mkOption {
      type = types.attrsOf credential;
      default = { };
      example = lib.literalExpression example;
      description = ''
        Secrets granted as systemd credentials, keyed by the name of the
        environment variable a secret replaces. A path, e.g. a sops-nix or
        agenix secret, uses `LoadCredential=`, the attribute form selects
        `LoadCredentialEncrypted=` with `encrypted = true` or, without
        `source`, `ImportCredential=` from the system credential store, which
        every service shares, so only for prefixed names like `HIVEGENT_*`.
        systemd copies a loaded file into the unit's private credential
        directory at start, so a missing file fails the start instead of
        running without the secret.
      '';
    };

  # The unit's credential directives, importing `imported` globs besides the
  # credentials without a `source`.
  serviceConfig =
    {
      credentials,
      imported ? [ ],
    }:
    let
      load =
        encrypted:
        lib.mapAttrsToList (name: value: "${name}:${value.source}") (
          lib.filterAttrs (_: value: value.source != null && value.encrypted == encrypted) credentials
        );
    in
    {
      LoadCredential = load false;
      LoadCredentialEncrypted = load true;
      ImportCredential =
        imported ++ lib.attrNames (lib.filterAttrs (_: value: value.source == null) credentials);
    };
}
