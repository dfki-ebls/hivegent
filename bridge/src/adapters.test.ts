import { describe, expect, it } from "vitest";

import { enabledAdapterNames } from "./adapters.js";

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
