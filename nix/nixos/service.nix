{
  lib,
  pkgs,
  config,
  utils,
  ...
}:
let
  cfg = config.services.hivegent;

  tomlFormat = pkgs.formats.toml { };
  configFile = tomlFormat.generate "hivegent-config.toml" cfg.settings;

  # `enableTorchCompile` is an argument of the hivegent backend derivation:
  # it puts the C toolchain TorchInductor needs for runtime codegen on the
  # wrapper's PATH and flips the DOCLING_INFERENCE_COMPILE_TORCH_MODELS
  # default — the toolchain must match the torch wheels pinned in the
  # backend, so it is the package's concern, not the unit's.
  package =
    if cfg.torchCompile then cfg.package.override { enableTorchCompile = true; } else cfg.package;
in
{
  options.services.hivegent = {
    enable = lib.mkEnableOption "the Hivegent backend service";

    package = lib.mkPackageOption pkgs "hivegent" {
      # No pkgs-level default: the flake's default module supplies the
      # per-system backend build via `mkDefault` (see `nix/default.nix`).
      # `default = null` with the implicit `nullable = false` keeps the
      # option required without looking up a (nonexistent) `pkgs.hivegent`.
      default = null;
      extraDescription = ''
        Provides `bin/hivegent`. The wrapper must already contain every CLI
        tool the backend invokes via subprocesses (jq, pandoc, ripgrep,
        libreoffice). Defaults to the `backend` package of the hivegent flake
        for the host platform.
      '';
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = ''
        Address `hivegent serve` binds to. Stays on the loopback by default;
        external traffic should reach the API through a reverse proxy.
      '';
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8000;
      description = ''
        Port `hivegent serve` listens on. Mirrored into
        `mcp.base_url`'s default and into the systemd `SocketBindAllow`.
      '';
    };

    settings = lib.mkOption {
      type = tomlFormat.type;
      default = { };
      example = lib.literalExpression ''
        {
          auth = {
            enable = true;
            issuer = "https://auth.example.com";
          };
          llm = {
            model = "qwen3.6-35b-a3b";
            base_url = "http://localhost:18000/v1";
          };
        }
      '';
      description = ''
        Hivegent TOML config rendered into the Nix store and passed via
        `HIVEGENT_CONFIG_FILE`. Nested attribute sets become TOML tables.

        Do NOT put secrets here — anything in this attrset lands in
        `/nix/store`. Use `environmentFile` for `HIVEGENT_*` overrides
        (e.g. `HIVEGENT_MCP__CLIENT_SECRET`); env vars take precedence
        over the TOML file.

        `auth.enable = false` bypasses JWT validation entirely and
        treats every request as a synthetic localhost user — only use
        that on developer workstations.

        `data_dir` defaults to the unit's `StateDirectory`; override
        here only if you need a different layout.
      '';
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      description = ''
        Plain environment variables set on the unit. Useful for non-secret
        `HIVEGENT_*` overrides of TOML values, or for unrelated vars like
        `HF_TOKEN` and proxy settings.
      '';
    };

    torchCompile = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Whether docling may wrap its torch document models in
        `torch.compile`. Rebuilds `package` with the toolchain
        TorchInductor needs for runtime codegen on the wrapper's PATH
        (`enableTorchCompile`) and persists the codegen cache under the
        unit's `CacheDirectory` so models compile once per torch version
        rather than on every restart. Off by default: eager inference is
        fast enough for these small models and avoids compile latency
        after every deploy.
      '';
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/etc/hivegent/hivegent.env";
      description = ''
        File in `KEY=VALUE` format forwarded via `EnvironmentFile`. Use it
        for secrets — API keys, MCP client secrets, OIDC client secrets —
        so they never land in the Nix store. Loaded by systemd before the
        process drops to the unit's `DynamicUser`, so the file must be
        readable by `root`. Missing files are tolerated (systemd's `-`
        prefix) so the unit still starts before an operator-managed
        secret file has been created.
      '';
    };

    postgresql.createLocally = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Whether to provision a local PostgreSQL database for Hivegent and
        wire `settings.db.url` to it.

        When enabled, defaults `settings.db.url` to a SQLAlchemy URL that
        reaches the host Postgres via the Unix socket at
        `/var/run/postgresql` as a `hivegent` role owning a `hivegent`
        database. Authentication relies on Postgres' `peer` rule, which
        works out of the box because the unit runs under
        `DynamicUser = true` (the resulting OS user name equals the unit
        name and is matched 1:1 against the DB role).

        Adds the role + database via `services.postgresql.ensureUsers` /
        `ensureDatabases` and orders the hivegent unit after
        `postgresql.target` so the setup oneshot has run first. Hivegent
        applies Alembic migrations during its FastAPI lifespan, so the
        database schema is brought to head on every restart — no
        separate deploy step or `ExecStartPre` is required. A failing
        migration aborts startup and trips the unit's
        `Restart = "on-failure"` policy.

        Requires `services.postgresql.enable = true` — this option only
        wires Hivegent *into* an existing Postgres, it does not turn one
        on.
      '';
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        assertions = [
          {
            assertion = cfg.postgresql.createLocally -> config.services.postgresql.enable;
            message =
              "`services.hivegent.postgresql.createLocally` requires " + "`services.postgresql.enable = true`.";
          }
        ];

        services.hivegent.settings.data_dir = lib.mkDefault "/var/lib/hivegent";

        systemd.services.hivegent = {
          description = "Hivegent backend";
          wantedBy = [ "multi-user.target" ];
          after = [ "network.target" ];

          environment = {
            # Avoid tool caches under `/var/empty`.
            HOME = "/var/lib/hivegent";
            HF_HOME = "/var/cache/hivegent";
            PYTHONUNBUFFERED = "1";
            HIVEGENT_CONFIG_FILE = "${configFile}";
          }
          // lib.optionalAttrs cfg.torchCompile {
            TORCHINDUCTOR_CACHE_DIR = "/var/cache/hivegent/inductor";
          }
          // cfg.environment;

          serviceConfig = {
            Type = "exec";
            Restart = "on-failure";
            RestartSec = 5;
            TimeoutStartSec = 600;
            UMask = "0077";

            DynamicUser = true;
            StateDirectory = "hivegent";
            CacheDirectory = "hivegent";
            WorkingDirectory = "/var/lib/hivegent";

            EnvironmentFile = lib.optional (cfg.environmentFile != null) "-${cfg.environmentFile}";

            ExecStart = utils.escapeSystemdExecArgs [
              (lib.getExe' package "hivegent")
              "serve"
              "--host"
              cfg.host
              "--port"
              (toString cfg.port)
            ];

            SocketBindDeny = "any";
            SocketBindAllow = "tcp:${toString cfg.port}";
            RestrictAddressFamilies = [
              "AF_INET"
              "AF_INET6"
              "AF_UNIX"
            ];

            CapabilityBoundingSet = "";
            AmbientCapabilities = "";
            NoNewPrivileges = true;
            # CUDA-backed document/OCR models need the host NVIDIA character devices.
            PrivateDevices = false;
            PrivateIPC = true;
            PrivateMounts = true;
            PrivateTmp = true;
            PrivateUsers = true;
            ProtectClock = true;
            ProtectControlGroups = true;
            ProtectHome = true;
            ProtectHostname = true;
            ProtectKernelLogs = true;
            ProtectKernelModules = true;
            ProtectKernelTunables = true;
            ProtectProc = "invisible";
            ProtectSystem = "strict";
            LockPersonality = true;
            RemoveIPC = true;
            RestrictNamespaces = true;
            RestrictRealtime = true;
            RestrictSUIDSGID = true;
            SystemCallArchitectures = "native";
            SystemCallFilter = [
              "@system-service"
              "~@privileged"
              "~@resources"
            ];
            SystemCallErrorNumber = "EPERM";
          };

          unitConfig = {
            StartLimitBurst = 5;
            StartLimitIntervalSec = 600;
          };
        };
      }

      (lib.mkIf cfg.postgresql.createLocally {
        # asyncpg resolves the `host` directory to the Postgres socket.
        services.hivegent.settings.db.url =
          lib.mkDefault "postgresql+psycopg://hivegent@/hivegent?host=/var/run/postgresql";

        services.postgresql = {
          ensureDatabases = [ "hivegent" ];
          ensureUsers = [
            {
              name = "hivegent";
              ensureDBOwnership = true;
            }
          ];
        };

        # pgvector's `vector` extension is untrusted, so the non-superuser
        # `hivegent` role cannot create it during its Alembic migrations.
        # Create it as the `postgres` superuser right after the database is
        # provisioned (this runs in the same oneshot that `ensureDatabases`
        # uses); hivegent's `CREATE EXTENSION IF NOT EXISTS vector` then
        # short-circuits before its privilege check and succeeds. Requires
        # `services.postgresql.extensions` to provide `pgvector`.
        systemd.services.postgresql-setup.script = lib.mkAfter ''
          psql -d hivegent -tAc 'CREATE EXTENSION IF NOT EXISTS vector'
        '';

        systemd.services.hivegent = {
          after = [ "postgresql.target" ];
          requires = [ "postgresql.target" ];
        };
      })
    ]
  );
}
