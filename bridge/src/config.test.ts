import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { resolveConfig, secretSource } from "./config.js";

const secrets = (values: Record<string, string>) => (name: string) => values[name];

describe("secretSource", () => {
  it("prefers the variable, then the prefixed credential file", () => {
    const dir = mkdtempSync(join(tmpdir(), "credentials-"));
    writeFileSync(join(dir, "hivegent-bridge.OIDC_CLIENT_SECRET"), "s3cret\n");
    writeFileSync(join(dir, "hivegent.POSTGRES_URL"), "backend\n");
    const source = secretSource({ CREDENTIALS_DIRECTORY: dir, TEAMS_APP_PASSWORD: "env" });

    expect(source("OIDC_CLIENT_SECRET")).toBe("s3cret");
    expect(source("TEAMS_APP_PASSWORD")).toBe("env");
    expect(source("POSTGRES_URL")).toBeUndefined();
    expect(secretSource({})("POSTGRES_URL")).toBeUndefined();
  });
});

describe("resolveConfig", () => {
  const base = { backendUrl: "http://127.0.0.1:8000", adapters: { teams: false } };

  it("applies defaults and attaches the secrets of the configured sections", () => {
    const cfg = resolveConfig(
      { ...base, oidc: { issuer: "https://idp", clientId: "bot" } },
      secrets({ POSTGRES_URL: "postgres://x", OIDC_CLIENT_SECRET: "s" }),
    );

    expect(cfg).toEqual({
      backendUrl: "http://127.0.0.1:8000",
      host: "127.0.0.1",
      port: 3003,
      botUserName: "hivegent",
      adapters: { teams: false, web: false },
      postgresUrl: "postgres://x",
      oidc: { issuer: "https://idp", clientId: "bot", clientSecret: "s" },
      teams: undefined,
    });
  });

  it("rejects invalid or unknown settings and missing secrets", () => {
    const ok = secrets({ POSTGRES_URL: "postgres://x" });

    expect(() => resolveConfig({ ...base, backendUrl: "https://backend/" }, ok)).toThrow("slash");
    expect(() => resolveConfig({ ...base, backendUrl: "http://backend" }, ok)).toThrow("loopback");
    expect(() =>
      resolveConfig({ ...base, oidc: { issuer: "http://idp", clientId: "bot" } }, ok),
    ).toThrow("loopback");
    expect(() => resolveConfig({ ...base, port: "3003" }, ok)).toThrow("expected number");
    expect(() => resolveConfig({ ...base, hivegentUrl: "http://backend" }, ok)).toThrow(
      "hivegentUrl",
    );
    expect(() =>
      resolveConfig({ ...base, teams: { appId: "a", tenantId: "t", allowedTenants: [] } }, ok),
    ).toThrow(">=1");
    expect(() => resolveConfig(base, secrets({}))).toThrow("POSTGRES_URL");
    expect(() =>
      resolveConfig(
        {
          ...base,
          adapters: { teams: false, web: true },
          oidc: { issuer: "https://idp", clientId: "bot" },
        },
        secrets({ POSTGRES_URL: "postgres://x", OIDC_CLIENT_SECRET: "s" }),
      ),
    ).toThrow("without oidc");
  });
});
