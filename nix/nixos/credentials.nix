# Shared `credentials` option of the Hivegent units. Each systemd credential is
# named `<component>.<NAME>` in the style of systemd's own credentials, where
# `component` must match the prefix the service reads, and is read by the
# service from `$CREDENTIALS_DIRECTORY`, so secrets never enter the Nix store or
# the unit's environment, and only the unit's (dynamic) user can read them.
lib: component: {
  option =
    example:
    lib.mkOption {
      type = lib.types.attrsOf lib.types.externalPath;
      default = { };
      example = lib.literalExpression example;
      description = ''
        Secrets loaded as credentials `${component}.<NAME>` from files outside the
        Nix store, e.g. sops-nix or agenix secrets.
        systemd copies each file into the unit's private credential directory
        at start, so a missing file fails the start instead of running without
        the secret.
        Every `${component}.*` credential in the system credential store, plain in
        `/etc/credstore` or encrypted with `systemd-creds encrypt --name=<name>`
        in `/etc/credstore.encrypted`, is imported without an entry, where a
        missing or undecryptable one is skipped with a warning.
      '';
    };

  # The unit's credential directives, to merge into its `serviceConfig`.
  serviceConfig = credentials: {
    LoadCredential = lib.mapAttrsToList (name: path: "${component}.${name}:${path}") credentials;
    ImportCredential = [ "${component}.*" ];
  };
}
