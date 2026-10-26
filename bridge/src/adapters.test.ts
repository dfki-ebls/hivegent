import { describe, expect, it, vi } from "vitest";

import { ADAPTER_REGISTRY, enabledAdapterNames } from "./adapters.js";
import type { BridgeConfig } from "./config.js";

describe("enabledAdapterNames", () => {
  it("applies per-adapter registry defaults when unset (teams on, web off)", () => {
    expect(enabledAdapterNames({})).toEqual(["teams"]);
  });

  it("lets an override flip an adapter on or off", () => {
    expect(enabledAdapterNames({ web: true })).toEqual(["teams", "web"]);
    expect(enabledAdapterNames({ teams: false })).toEqual([]);
    expect(enabledAdapterNames({ teams: false, web: true })).toEqual(["web"]);
  });
});

describe("web adapter", () => {
  it("refuses to run in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    await expect(ADAPTER_REGISTRY.web!.create({} as BridgeConfig)).rejects.toThrow("production");
    vi.unstubAllEnvs();
  });
});
