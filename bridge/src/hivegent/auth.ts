/** OIDC client-credentials token provider with single-flight refresh. */

import { posix } from "node:path";

import { isSecureEndpoint, type OidcConfig } from "../config.js";
import { fetchRetryable, HttpError } from "./retry.js";

interface CachedToken {
  accessToken: string;
  /** When the token is refreshed, halfway through its lifetime. */
  refreshAt: number;
}

interface TokenResponse {
  access_token: string;
  expires_in?: number;
}

// Bounds each IdP round-trip, since a hung one would stall every turn behind it.
const IDP_TIMEOUT_MS = 10_000;

export class ServiceTokenProvider {
  readonly #config: OidcConfig;
  #tokenEndpoint: string | null = null;
  #cached: CachedToken | null = null;
  #inflight: Promise<string> | null = null;

  constructor(config: OidcConfig) {
    this.#config = config;
  }

  /**
   * Return an access token with at least half its lifetime left, refreshing
   * (once, shared) past that, so a turn rarely outlives the token it opened with.
   */
  async getToken(): Promise<string> {
    if (this.#cached && this.#cached.refreshAt > Date.now()) {
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

    const url = new URL(this.#config.issuer);
    url.pathname = posix.join(url.pathname, ".well-known/openid-configuration");

    if (!isSecureEndpoint(url)) {
      throw new Error(`OIDC discovery URL ${url.href} is insecure`);
    }

    const res = await fetchRetryable(url, { signal: AbortSignal.timeout(IDP_TIMEOUT_MS) });

    if (!res.ok) {
      throw new HttpError(res.status, `OIDC discovery failed (${res.status}) at ${url.href}`);
    }

    const doc = (await res.json()) as { issuer?: string; token_endpoint?: string };

    // OpenID Connect Discovery 1.0, section 4.3.
    if (doc.issuer !== this.#config.issuer) {
      throw new Error(`OIDC discovery issuer ${doc.issuer} does not match ${this.#config.issuer}`);
    }

    const endpoint = doc.token_endpoint && URL.parse(doc.token_endpoint);

    if (!endpoint || !isSecureEndpoint(endpoint)) {
      throw new Error(`OIDC discovery token_endpoint ${doc.token_endpoint} is missing or insecure`);
    }

    this.#tokenEndpoint = endpoint.href;
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

    const res = await fetchRetryable(endpoint, {
      method: "POST",
      headers: {
        Authorization: `Basic ${basic}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body,
      signal: AbortSignal.timeout(IDP_TIMEOUT_MS),
    });

    if (!res.ok) {
      throw new HttpError(res.status, `client_credentials token request failed (${res.status})`);
    }

    const data = (await res.json()) as TokenResponse;
    const expiresIn = data.expires_in ?? 300;

    this.#cached = {
      accessToken: data.access_token,
      refreshAt: Date.now() + (expiresIn * 1000) / 2,
    };

    return this.#cached.accessToken;
  }
}
