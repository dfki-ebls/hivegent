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

  hardening = import ./hardening.nix;
  credentials = import ./credentials.nix lib "hivegent";

  # `enableTorchCompile` is an argument of the hivegent backend derivation: it
  # puts the C toolchain TorchInductor needs for runtime codegen on the wrapper's
  # PATH and flips the DOCLING_INFERENCE_COMPILE_TORCH_MODELS default. It must
  # match the torch wheels pinned in the backend, so it is the package's concern,
  # not the unit's.
  # `plugins` is one too, since the wrapper puts them on the interpreter's path.
  # Without either, a custom package that has no `override` is used as is.
  packageOverrides =
    lib.optionalAttrs cfg.torchCompile { enableTorchCompile = true; }
    // lib.optionalAttrs (cfg.plugins != [ ]) { inherit (cfg) plugins; };
  package = if packageOverrides == { } then cfg.package else cfg.package.override packageOverrides;
  egressProxy = cfg.egressProxy.package.onLoopback cfg.egressProxy.port;
  searxPort = config.services.searx.settings.server.port or 8888;
  hivegent = lib.getExe' package "hivegent";

  # The environment, identity, and sandbox of `hivegent serve`, built once and
  # shared by the service and `hivegent-manage`, so a host command sees exactly
  # the service's files, database, credentials, and limits.
  serviceEnvironment = {
    # Avoid tool caches under `/var/empty`.
    HOME = "/var/lib/hivegent";
    HF_HOME = "/var/cache/hivegent";
    # Third-party caches stay off the backed-up state directory.
    XDG_CACHE_HOME = "/var/cache/hivegent";
    PYTHONUNBUFFERED = "1";
    HIVEGENT_CONFIG_FILE = "${configFile}";
  }
  // lib.optionalAttrs (cfg.allowedAddresses != null) {
    # Third-party downloaders such as the Hugging Face hub fetch models
    # through the egress proxy.
    # Hivegent's own clients ignore these (`trust_env=False`).
    # Lowercase, since curl and wget ignore the uppercase variants.
    http_proxy = egressProxy.url;
    https_proxy = egressProxy.url;
    no_proxy = "localhost,127.0.0.1,::1";
  }
  // lib.optionalAttrs cfg.torchCompile {
    TORCHINDUCTOR_CACHE_DIR = "/var/cache/hivegent/inductor";
  }
  // cfg.environment;

  identity =
    credentials.serviceConfig cfg.credentials
    // hardening
    // {
      # Named, so the service and every `hivegent-manage` run share one dynamic user.
      DynamicUser = true;
      User = "hivegent";
      StateDirectory = "hivegent";
      CacheDirectory = "hivegent";
      WorkingDirectory = "/var/lib/hivegent";

      EnvironmentFile = lib.optional (cfg.environmentFile != null) cfg.environmentFile;
    }
    // lib.optionalAttrs (cfg.allowedAddresses != null) {
      IPAddressDeny = "any";
      IPAddressAllow = [ "localhost" ] ++ cfg.allowedAddresses;
    };

  # `identity` as `systemd-run` properties, a list setting one per item.
  property =
    name: value:
    "--property=${name}=${if lib.isBool value then lib.boolToString value else toString value}";
  runArgs =
    lib.mapAttrsToList (name: value: "--setenv=${name}=${toString value}") serviceEnvironment
    ++ lib.concatLists (
      lib.mapAttrsToList (name: value: map (property name) (lib.toList value)) identity
    );

  manage = pkgs.callPackage ../manage.nix { } {
    runtimeInputs = [
      config.systemd.package
      pkgs.coreutils
    ];
    prelude = ''
      if [ "$(id -u)" -ne 0 ]; then
        echo "hivegent-manage runs hivegent as the hivegent service and needs root, e.g. sudo hivegent-manage reset workspace" >&2
        exit 1
      fi
    '';
    run = "systemd-run --pty --wait --collect --quiet ${lib.escapeShellArgs runArgs} -- ${hivegent}";
    running = "systemctl is-active --quiet hivegent.service";
    stop = "systemctl stop hivegent.service";
    start = "systemctl start hivegent.service";
  };
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
        tool the backend invokes via subprocesses (jq, poppler, ripgrep).
        Defaults to the `backend` package of the hivegent flake
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
        Port `hivegent serve` listens on. Mirrored into the systemd
        `SocketBindAllow`.
      '';
    };

    egressProxy = {
      package = lib.mkPackageOption pkgs "smokescreen" {
        default = null;
        extraDescription = ''
          Defaults to the Smokescreen package built by the hivegent flake.
        '';
      };

      port = lib.mkOption {
        type = lib.types.port;
        default = cfg.egressProxy.package.passthru.defaultPort;
        description = ''
          Loopback port for the SSRF-safe outbound HTTP CONNECT proxy.
        '';
      };
    };

    allowedAddresses = lib.mkOption {
      type = lib.types.nullOr (lib.types.listOf lib.types.str);
      default = null;
      example = [ "10.0.0.0/8" ];
      description = ''
        `IPAddressAllow` entries the unit may exchange traffic with besides
        loopback. `null` leaves the unit's network unrestricted.

        A list denies every other address, inbound included, so a non-loopback
        `host` needs its clients listed. Operator-configured endpoints such as
        `auth.issuer`, an LLM provider's `base_url` or a TCP `db.url` must then be on
        loopback, a unix socket, or this list. Third-party clients such as the
        Hugging Face hub are pointed at the egress proxy instead, which also
        covers FastMCP's OIDC clients, so an issuer resolving to loopback
        needs its host appended to `environment.no_proxy`.
      '';
    };

    settings = lib.mkOption {
      type = tomlFormat.type;
      default = { };
      example = lib.literalExpression ''
        {
          auth = {
            issuer = "https://auth.example.com";
            audience = [ "hivegent-spa" ];
          };
          llm = {
            default = "qwen";
            providers.local = {
              base_url = "http://localhost:18000/v1";
              inference_provider = "llama.cpp";
            };
            models.qwen = {
              provider = "local";
              model = "qwen3.8-27b";
              family = "qwen";
              label = "Qwen 3.8 27B";
              reasoning = [ "low" "medium" "high" ];
              reasoning_overrides.high = "xhigh";
              vision = true;
            };
          };
          skills.dir = "''${./skills}";
          mcp_servers.crm = {
            url = "https://crm.example.com/mcp";
            groups = [ "sales" ];
            effects.find_contact = "read";
            oauth = {
              client_id = "hivegent";
              issuer = "https://id.example.com";
            };
          };
          sources.handbook = {
            connector = "webdav";
            url = "https://cloud.example.com/remote.php/dav/files/hivegent/Handbook/";
            username = "hivegent";
            groups = [ "sales" ];
          };
        }
      '';
      description = ''
        Hivegent TOML config rendered into the Nix store and passed via
        `HIVEGENT_CONFIG_FILE`. Nested attribute sets become TOML tables.

        Do NOT put secrets here, anything in this attrset lands in
        `/nix/store`. Pass them as `credentials` instead, which take
        precedence over the TOML file, such as an LLM provider's key as
        `LLM__PROVIDERS__<ID>__API_KEY`.

        `llm` holds the model catalog: `providers` and `models` keyed by id,
        and the roles `default`, `aux`, and `stt` naming models by id.
        Users pick only among the catalog's chat models.

        `auth.issuer` is required, `hivegent serve` refuses to start without
        it. Only `hivegent dev`, never run by this module, has a development user.
        `mcp.base_url` serves the `/mcp` endpoint.

        `mcp_servers.<id>` connects an external MCP server, whose tools the
        agent is offered as `<id>_<tool>`, with a `url` for a Streamable
        HTTP endpoint and a `command` for a subprocess, as in MCP client configs. An HTTP server has its
        secrets as `credentials` such as `MCP_SERVERS__<ID>__OAUTH__CLIENT_SECRET`
        or `MCP_SERVERS__<ID>__SECRET_HEADERS__<NAME>`, and its host is an
        operator endpoint, so a non-null `allowedAddresses` must cover it.
        A stdio server has its secrets as `MCP_SERVERS__<ID>__SECRET_ENV__<NAME>`
        and runs with the unit's rights, so `mcpPackages` must provide its
        `command`, and a server that needs the network needs `allowedAddresses`
        to cover its hosts, since it bypasses the egress proxy.

        `sources.<id>` mirrors an external file store, such as a Nextcloud
        folder over WebDAV, to `/mnt/<id>` for the members of its `groups`,
        with its password as the `credentials` entry
        `SOURCES__<ID>__PASSWORD`, a Nextcloud app password for one. Its host
        is an operator endpoint too, so a non-null `allowedAddresses` must
        cover it.

        `skills.dir` names a directory of global skills, one folder with a
        `SKILL.md` each, read in place, so a path in the store works.
        `assistants.dir` likewise names a directory of global assistants,
        one `<name>.json` file each.

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

    limits = {
      maxFileSizeBytes = lib.mkOption {
        type = lib.types.ints.positive;
        default = 50 * 1024 * 1024;
        description = ''
          Maximum size in bytes of a single uploaded file. Fed to the backend
          `settings.limits.max_file_size_bytes` and, when the Caddy vhost is
          enabled, used to size its per-request body cap just above this value.
          Set it here so the backend enforcer and the edge cap move together;
          the default mirrors the backend's own.
        '';
      };

      maxCollectionSizeBytes = lib.mkOption {
        type = lib.types.ints.positive;
        default = 512 * 1024 * 1024;
        description = ''
          Maximum decompressed size in bytes of an uploaded collection ZIP. Fed
          to the backend `settings.limits.max_collection_size_bytes` and used to
          size the Caddy body cap for `/api/v1/documents/collections/*` just above
          it. The default mirrors the backend's own.
        '';
      };
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

    plugins = lib.mkOption {
      type = lib.types.listOf lib.types.path;
      default = [ ];
      example = lib.literalExpression "[ ./hivegent-lookup ]";
      description = ''
        Project directories of Python packages, each with a `pyproject.toml`,
        that advertise `hivegent.plugins` entry points. Rebuilds `package`
        with them in its virtualenv (`plugins`), so they run on its
        interpreter and take their dependencies from its lock. A dependency
        the backend lacks or holds at a version a plugin refuses, and a module
        another package installs too, fail the build.

        An installed plugin loads only once `settings.plugins.enabled` names it,
        with its options from `settings.plugins.config.<name>` and its secrets
        from `credentials` such as `PLUGINS__CONFIG__<NAME>__API_KEY`. A plugin
        that is enabled but missing or failing stops the service at startup.
        Plugins run in-process with the service's rights, so install only
        trusted ones.
      '';
    };

    mcpPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ ];
      example = lib.literalExpression "[ pkgs.nodejs pkgs.uv ]";
      description = ''
        Packages whose `bin` directories join the unit's `PATH`, so the
        `command` of a `kind = "stdio"` entry of `settings.mcp_servers`
        resolves, such as `npx` or `uvx`. The server runs as the unit under
        its sandbox, and a command that downloads or reaches the network needs
        the addresses it uses in `allowedAddresses`.
      '';
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      example = "/etc/hivegent/hivegent.env";
      description = ''
        Shortcut for the unit's `EnvironmentFile=`, a `KEY=VALUE` file read at
        start that overrides `environment` and the TOML file.
        Prefer `credentials` for secrets.
      '';
    };

    credentials = credentials.option ''
      {
        LLM__PROVIDERS__LOCAL__API_KEY = "/run/secrets/hivegent-llm-api-key";
        TRANSPARENCY__SECRET_KEY = "/run/secrets/hivegent-transparency-secret-key";
        MCP__CLIENT_SECRET = "/run/secrets/hivegent-mcp-client-secret";
        MCP__CONFIRMATION_KEY = "/run/secrets/hivegent-mcp-confirmation-key";
        MCP_SERVERS__CRM__OAUTH__CLIENT_SECRET = "/run/secrets/hivegent-crm-client-secret";
        SOURCES__HANDBOOK__PASSWORD = "/run/secrets/hivegent-handbook-password";
      }
    '';

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
        database schema is brought to head on every restart, no
        separate deploy step or `ExecStartPre` is required. A failing
        migration aborts startup, which the unit retries.

        Requires `services.postgresql.enable = true`, this option only
        wires Hivegent *into* an existing Postgres, it does not turn one
        on.
      '';
    };

    searxng.createLocally = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Whether to point Hivegent's web tools at the local `services.searx`,
        adding `json` to its `search.formats`. Under
        `services.searx.configureUwsgi`, set `settings.tools.search_url` to
        the uWSGI listener yourself.

        Requires `services.searx.enable = true` with a secret key, e.g. a
        `SEARXNG_SECRET` from `services.searx.environmentFile`, this option
        only wires Hivegent *into* an existing SearXNG, it does not turn one on.
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
          {
            assertion = cfg.searxng.createLocally -> config.services.searx.enable;
            message = "`services.hivegent.searxng.createLocally` requires `services.searx.enable = true`.";
          }
        ];

        warnings =
          lib.optional (cfg.plugins != [ ] && (cfg.settings.plugins.enabled or [ ]) == [ ])
            "`services.hivegent.plugins` installs plugins, but `services.hivegent.settings.plugins.enabled` names none, so none loads.";

        # Host administration (`reset`, `migrate`) as the service runs, root only.
        environment.systemPackages = [ manage ];

        services.hivegent.settings.data_dir = lib.mkDefault "/var/lib/hivegent";
        services.hivegent.settings.security.egress_proxy_url = lib.mkDefault egressProxy.url;

        # The upload limits are the single source of truth: feed them to the
        # backend enforcer here (mkDefault, so a direct `settings.limits`
        # override still wins) while `caddy.nix` sizes the edge body caps from
        # the same `cfg.limits.*`.
        services.hivegent.settings.limits = {
          max_file_size_bytes = lib.mkDefault cfg.limits.maxFileSizeBytes;
          max_collection_size_bytes = lib.mkDefault cfg.limits.maxCollectionSizeBytes;
        };

        systemd.services.hivegent = {
          description = "Hivegent backend";
          wantedBy = [ "multi-user.target" ];
          after = [
            "network.target"
            "hivegent-egress-proxy.service"
          ];
          requires = [ "hivegent-egress-proxy.service" ];

          environment = serviceEnvironment;
          path = cfg.mcpPackages;

          serviceConfig = identity // {
            Type = "exec";
            # A server that lost the database connection holding its locks stops
            # itself with SIGTERM, a clean exit only `always` restarts, while
            # `systemctl stop` still keeps it down. Restarts back off to one a
            # minute, which stays below the start limit through an outage of any length.
            Restart = "always";
            RestartSec = 5;
            RestartSteps = 5;
            RestartMaxDelaySec = 60;
            TimeoutStartSec = 600;
            # Must exceed uvicorn's `timeout_graceful_shutdown` (30s) so teardown finishes before SIGKILL.
            TimeoutStopSec = 45;

            ExecStart = utils.escapeSystemdExecArgs [
              hivegent
              "serve"
              "--host"
              cfg.host
              "--port"
              (toString cfg.port)
            ];

            SocketBindAllow = "tcp:${toString cfg.port}";
          };

          unitConfig = {
            StartLimitBurst = 20;
            StartLimitIntervalSec = 600;
          };
        };

        systemd.services.hivegent-egress-proxy = {
          description = "Hivegent SSRF-safe outbound proxy";
          wantedBy = [ "multi-user.target" ];
          after = [ "network.target" ];

          serviceConfig = hardening // {
            Type = "exec";
            DynamicUser = true;
            Restart = "on-failure";
            RestartSec = 5;
            ExecStart = lib.getExe egressProxy.package;
            SocketBindAllow = "tcp:${toString cfg.egressProxy.port}";
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
        # uses), and hivegent's `CREATE EXTENSION IF NOT EXISTS vector` then
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

      (lib.mkIf cfg.searxng.createLocally {
        services.hivegent.settings.tools.search_url =
          lib.mkDefault "http://127.0.0.1:${toString searxPort}";
        # Merged with the formats the host serves its own users.
        services.searx.settings.search.formats = [ "json" ];

        # Search is optional to the app, so a failed SearXNG fails the
        # `web_search` calls rather than the backend.
        systemd.services.hivegent = {
          after = [ "searx.service" ];
          wants = [ "searx.service" ];
        };
      })
    ]
  );
}
