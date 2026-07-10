# Caddy site body shared by the NixOS vhost module (`nixos/caddy.nix`) and the
# proxy Docker image (`docker.nix`).  It carries only what Hivegent itself
# needs — reverse proxying the API, serving the SPA, and the security headers
# its OIDC/speech flows require.  Site-wide hardening such as geoblocking or
# scanner honeypots is deployment policy and is injected verbatim through
# `extraConfig`, so the same body backs both a hardened production vhost and a
# bare container.
{
  lib,
  # Backend upstream as a Caddy dial address (`host:port`).
  upstream,
  # Chat-bridge upstream (`host:port`) for `/api/webhooks/*`, or `null` to route
  # that path to the backend like the rest of `/api/*`.
  bridgeUpstream ? null,
  # Directory holding the built SPA (`index.html`, `assets/`), or `null` to
  # serve the API only and answer every other path with 404.
  frontend ? null,
  # Directory holding the built mdbook handbook, served as static files under
  # `docsPath`, or `null` to keep that path private (404). The book must be
  # built with `site-url` matching `docsPath` so its absolute links resolve.
  docs ? null,
  # URL prefix the handbook is mounted at, without a trailing slash.
  docsPath ? "/docs",
  # Whether to expose the `/mcp` endpoint or answer it with 404.
  enableMcp ? false,
  # Whether to emit HSTS (only meaningful when the vhost is served over TLS).
  enableHsts ? false,
  # Backend upload limits in bytes; the edge body caps are sized just above
  # them. Mirror the backend `limits.max_file_size_bytes` and
  # `limits.max_collection_size_bytes` — the NixOS module passes
  # `services.hivegent.limits.*` so the enforcer and the cap always move
  # together; the defaults match the backend's own.
  maxFileSizeBytes ? 50 * 1024 * 1024,
  maxCollectionSizeBytes ? 512 * 1024 * 1024,
  # Operator snippet placed before the route handlers — geoblocking, IP
  # allow-lists, honeypots.  A blocked `handle` here wins over the API/SPA
  # handlers below because Caddy evaluates mutually-exclusive `handle` groups
  # in source order.
  extraConfig ? "",
}:
let
  # Edge body cap for a backend limit: 10% headroom absorbs multipart/form-data
  # framing (boundaries, headers, other form fields) so a legitimately sized
  # upload is never rejected at the edge, while an egregiously oversized one is
  # still refused before it spools. `request_body` reads a bare integer as bytes.
  edgeCap = limit: toString (limit + limit / 10);
in
''
  # oidc-spa restores sessions through a hidden same-origin iframe, so this
  # vhost must permit framing by itself for silent session restoration.
  header {
    X-Content-Type-Options nosniff
    X-Frame-Options SAMEORIGIN
    Referrer-Policy strict-origin-when-cross-origin
    Content-Security-Policy "frame-ancestors 'self'"
    -Server
    ${lib.optionalString enableHsts ''
      Strict-Transport-Security "max-age=31536000; includeSubDomains"
    ''}
  }
  # Speech input records from the microphone; camera and geolocation are unused.
  header Permissions-Policy "camera=(), microphone=(self), geolocation=()"

  ${extraConfig}

  encode

  # Keep large uploads limited to API routes.
  @small_body not path /api/*
  request_body @small_body {
    max_size 1MB
  }

  # FastAPI's interactive API surfaces stay private regardless of the handbook.
  @apidocs path /redoc* /openapi.json
  handle @apidocs {
    respond 404
  }

  ${
    if docs == null then
      ''
        # No handbook bundled: keep its path private too.
        @docs path ${docsPath} ${docsPath}/*
        handle @docs {
          respond 404
        }
      ''
    else
      ''
        # Static mdbook handbook. Bare `${docsPath}` redirects to the
        # trailing-slash root where the language redirect lives; `strip_prefix`
        # maps the remaining request onto the book's own layout.
        redir ${docsPath} ${docsPath}/
        handle ${docsPath}/* {
          uri strip_prefix ${docsPath}
          root * ${docs}
          file_server
        }
      ''
  }

  ${lib.optionalString (bridgeUpstream != null) ''
    # Chat-platform webhooks are handled by the bridge, not the API backend.
    # Placed before `/api/*` so its mutually-exclusive `handle` group wins.
    handle /api/webhooks/* {
      reverse_proxy ${bridgeUpstream} {
        flush_interval -1
      }
    }
  ''}

  handle /api/* {
    # Coarse body-size caps at the edge, derived from the backend's own limits
    # (see `edgeCap`) so oversized uploads are rejected before they spool.
    # Collections upload a ZIP (`maxCollectionSizeBytes`); every other API call
    # sits near the per-file limit (`maxFileSizeBytes`). The matchers are
    # mutually exclusive on purpose: `request_body` stacks MaxBytesReaders, so
    # overlapping matchers would let the smaller cap shadow the larger — a later
    # directive cannot raise it. The backend stays the precise enforcer (exact
    # file size, and the collection's decompressed size, file count, and path
    # safety Caddy cannot see).
    @collection path /api/documents/collections/*
    request_body @collection {
      max_size ${edgeCap maxCollectionSizeBytes}
    }
    @small not path /api/documents/collections/*
    request_body @small {
      max_size ${edgeCap maxFileSizeBytes}
    }
    header Content-Security-Policy "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    reverse_proxy ${upstream} {
      flush_interval -1
      health_uri      /api/health
      health_interval 10s
      health_timeout  3s
    }
  }

  ${
    if enableMcp then
      ''
        handle /mcp* {
          reverse_proxy ${upstream} {
            flush_interval -1
          }
        }
      ''
    else
      ''
        @mcp path /mcp /mcp/*
        handle @mcp {
          respond 404
        }
      ''
  }

  ${
    if frontend == null then
      ''
        # API-only deployment: nothing left to serve, so 404 every non-API path.
        handle {
          respond 404
        }
      ''
    else
      ''
        handle /assets/* {
          root * ${frontend}
          header Cache-Control "public, max-age=31536000, immutable"
          header {
            -ETag
            -Last-Modified
          }
          file_server
        }

        handle {
          root * ${frontend}
          header Cache-Control "no-store"
          header {
            -ETag
            -Last-Modified
          }
          try_files {path} /index.html
          file_server
        }
      ''
  }
''
