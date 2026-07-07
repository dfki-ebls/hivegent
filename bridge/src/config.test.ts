import { describe, expect, it } from "vitest";

import { resolveConfig } from "./config.js";

describe("resolveConfig", () => {
  it("layers env over the file and requires the essentials", () => {
    const file = {
      hivegentUrl: "http://file/",
      oidc: { issuer: "http://idp", clientId: "from-file", clientSecret: "s" },
      host: "0.0.0.0",
      port: 4000,
      adapters: { teams: false },
    };
    // Env overrides file for URL, host, and port; file supplies OIDC; ENABLE_WEB overrides adapters.
    const cfg = resolveConfig(
      {
        HIVEGENT_URL: "http://env",
        POSTGRES_URL: "postgres://x",
        HOST: "127.0.0.2",
        PORT: "5000",
        ENABLE_WEB: "true",
      },
      file,
    );

    expect(cfg.hivegentUrl).toBe("http://env");
    expect(cfg.host).toBe("127.0.0.2");
    expect(cfg.port).toBe(5000);
    expect(cfg.oidc).toEqual({ issuer: "http://idp", clientId: "from-file", clientSecret: "s" });
    expect(cfg.adapters).toEqual({ teams: false, web: true });
    expect(cfg.disabledTools).toEqual(["edit_document", "write_document", "save_memory"]);
  });

  it("lets an ENABLE_<NAME> env var override the file's adapter map", () => {
    const cfg = resolveConfig(
      { HIVEGENT_URL: "http://env", POSTGRES_URL: "postgres://x", ENABLE_TEAMS: "false" },
      { adapters: { teams: true } },
    );

    expect(cfg.adapters.teams).toBe(false);
  });

  it("omits OIDC when no client id is configured (auth-disabled debugging)", () => {
    const cfg = resolveConfig({ HIVEGENT_URL: "http://env", POSTGRES_URL: "postgres://x" }, {});

    expect(cfg.oidc).toBeUndefined();
    expect(cfg.host).toBe("127.0.0.1");
    expect(cfg.adapters).toEqual({});
  });

  it("throws when a required value is absent from both sources", () => {
    expect(() => resolveConfig({ HIVEGENT_URL: "http://env" }, {})).toThrow(/POSTGRES_URL/);
  });
});
