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
    const res = await fetch(url);

    if (!res.ok) {
      throw new Error(`OIDC discovery failed (${res.status}) at ${url}`);
    }

    const doc = (await res.json()) as { token_endpoint?: string };

    if (!doc.token_endpoint) {
      throw new Error("OIDC discovery document is missing token_endpoint");
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
