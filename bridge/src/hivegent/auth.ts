/** OIDC client-credentials token provider with single-flight refresh. */

import type { OidcConfig } from "../config.js";

interface CachedToken {
  accessToken: string;
  expiresAt: number;
}

interface TokenResponse {
  access_token: string;
  expires_in?: number;
}

const REFRESH_SKEW_MS = 30_000;
// Bounds each IdP round-trip, since a hung one would stall every turn behind it.
const IDP_TIMEOUT_MS = 10_000;

/** Whether the client secret may be sent to `url`: HTTPS, or HTTP on loopback for dev. */
function isSecureEndpoint(url: URL): boolean {
  return (
    url.protocol === "https:" ||
    (url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))
  );
}

export class ServiceTokenProvider {
  readonly #config: OidcConfig;
  #tokenEndpoint: string | null = null;
  #cached: CachedToken | null = null;
  #inflight: Promise<string> | null = null;

  constructor(config: OidcConfig) {
    this.#config = config;
  }

  /** Return a valid access token, refreshing (once, shared) when near expiry. */
  async getToken(): Promise<string> {
    if (this.#cached && this.#cached.expiresAt - REFRESH_SKEW_MS > Date.now()) {
      return this.#cached.accessToken;
    }

    this.#inflight ??= this.#refresh().finally(() => {
      this.#inflight = null;
    });

    return this.#inflight;
  }

  async #resolveTokenEndpoint(): Promise<string> {
    if (this.#tokenEndpoint) {
      return this.#tokenEndpoint;
    }

    const url = `${this.#config.issuer}/.well-known/openid-configuration`;
    const res = await fetch(url, { signal: AbortSignal.timeout(IDP_TIMEOUT_MS) });

    if (!res.ok) {
      throw new Error(`OIDC discovery failed (${res.status}) at ${url}`);
    }

    const doc = (await res.json()) as { issuer?: string; token_endpoint?: string };

    // OpenID Connect Discovery 1.0, section 4.3.
    if (doc.issuer?.replace(/\/+$/, "") !== this.#config.issuer) {
      throw new Error(`OIDC discovery issuer ${doc.issuer} does not match ${this.#config.issuer}`);
    }

    if (!doc.token_endpoint || !isSecureEndpoint(new URL(doc.token_endpoint))) {
      throw new Error(`OIDC discovery token_endpoint ${doc.token_endpoint} is missing or insecure`);
    }

    this.#tokenEndpoint = doc.token_endpoint;
    return this.#tokenEndpoint;
  }

  async #refresh(): Promise<string> {
    const endpoint = await this.#resolveTokenEndpoint();
    const body = new URLSearchParams({ grant_type: "client_credentials" });

    if (this.#config.scope) {
      body.set("scope", this.#config.scope);
    }

    const basic = Buffer.from(`${this.#config.clientId}:${this.#config.clientSecret}`).toString(
      "base64",
    );

    const res = await fetch(endpoint, {
      method: "POST",
      headers: {
        Authorization: `Basic ${basic}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body,
      signal: AbortSignal.timeout(IDP_TIMEOUT_MS),
    });

    if (!res.ok) {
      throw new Error(`client_credentials token request failed (${res.status})`);
    }

    const data = (await res.json()) as TokenResponse;
    const expiresIn = data.expires_in ?? 300;

    this.#cached = {
      accessToken: data.access_token,
      expiresAt: Date.now() + expiresIn * 1000,
    };

    return this.#cached.accessToken;
  }
}
