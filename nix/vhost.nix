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
  # Directory holding the built SPA (`index.html`, `assets/`), or `null` to
  # serve the API only and answer every other path with 404.
  frontend ? null,
  # Whether to expose the `/mcp` endpoint or answer it with 404.
  enableMcp ? false,
  # Whether to emit HSTS (only meaningful when the vhost is served over TLS).
  enableHsts ? false,
  # Operator snippet placed before the route handlers — geoblocking, IP
  # allow-lists, honeypots.  A blocked `handle` here wins over the API/SPA
  # handlers below because Caddy evaluates mutually-exclusive `handle` groups
  # in source order.
  extraConfig ? "",
}:
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

  encode zstd gzip

  # Keep large uploads limited to API routes.
  @small_body not path /api/*
  request_body @small_body {
    max_size 1MB
  }

  @docs path /docs* /redoc* /openapi.json
  handle @docs {
    respond 404
  }

  handle /api/* {
    request_body {
      max_size 60MB
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
