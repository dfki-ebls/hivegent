import type { Adapter, ChatInstance } from "chat";
import { describe, expect, it } from "vitest";

import { ADAPTER_REGISTRY, admits } from "./adapters.js";
import type { BridgeConfig } from "./config.js";

describe("teams adapter", () => {
  const oidc = { issuer: "https://idp", clientId: "bot", clientSecret: "s" };
  const teams = { appId: "app", tenantId: "ours", appPassword: "p", allowedTenants: ["ours"] };
  const cfg = { oidc, teams } as BridgeConfig;
  const post = (body: unknown, headers?: HeadersInit) =>
    new Request("http://bridge/api/webhooks/teams", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  const activity = (tenantId: string) => ({
    type: "message",
    serviceUrl: "https://smba.trafficmanager.net/teams",
    conversation: { id: "c", tenantId },
  });

  it("authenticates before it looks at the tenant", async () => {
    const adapter = ADAPTER_REGISTRY.teams.create(cfg) as Adapter;
    await adapter.initialize({} as ChatInstance);

    expect((await adapter.handleWebhook(post(activity("ours")))).status).toBe(401);
    expect((await adapter.handleWebhook(post(activity("theirs")))).status).toBe(401);
  });

  it("admits only messages of an allowed tenant", () => {
    expect(admits(cfg, "teams", activity("ours"))).toBe(true);
    expect(
      admits(cfg, "teams", { conversation: { id: "c" }, channelData: { tenant: { id: "ours" } } }),
    ).toBe(true);
    expect(admits(cfg, "teams", activity("theirs"))).toBe(false);
    expect(admits(cfg, "teams", {})).toBe(false);
    expect(admits(cfg, "web", {})).toBe(true);
  });

  it("refuses to run without a service client or a tenant allowlist", () => {
    const { teams: spec } = ADAPTER_REGISTRY;

    expect(() => spec.create({ teams } as BridgeConfig)).toThrow("`oidc`");
    expect(() => spec.create({ oidc } as BridgeConfig)).toThrow("`teams`");
  });
});
