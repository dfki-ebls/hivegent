import { afterEach, describe, expect, it, vi } from "vitest";

import type { BridgeConfig } from "../config.js";
import { buildRequestBody, chat, requireServicePrincipal } from "./client.js";

describe("buildRequestBody", () => {
  it("builds a schema-valid submit-message with one user text part", () => {
    const body = buildRequestBody("Hello");

    expect(body.trigger).toBe("submit-message");
    expect(typeof body.id).toBe("string");

    const messages = body.messages as Array<{ id: string; role: string; parts: unknown[] }>;
    expect(messages).toHaveLength(1);
    expect(messages[0]?.role).toBe("user");
    // hivegent stores the message under its id, which must be a canonical UUID.
    expect(messages[0]?.id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
    expect(messages[0]?.parts).toEqual([{ type: "text", text: "Hello" }]);
  });
});

describe("requireServicePrincipal", () => {
  const cfg = { backendUrl: "http://hivegent" } as BridgeConfig;

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("accepts a service principal and refuses a person", async () => {
    const principal = (kind: string) =>
      vi.fn<typeof fetch>(async () => Response.json({ id: "bot", kind }));

    vi.stubGlobal("fetch", principal("service"));
    await expect(requireServicePrincipal(cfg, "t")).resolves.toBeUndefined();

    vi.stubGlobal("fetch", principal("person"));
    await expect(requireServicePrincipal(cfg, "t")).rejects.toThrow("service_client_id");
  });
});

describe("chat", () => {
  const cfg = { backendUrl: "http://hivegent" } as BridgeConfig;

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("declares the service principal on every turn that carries a token", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response("ok"));
    vi.stubGlobal("fetch", fetchMock);

    await chat("c", "hi", "t", cfg);
    await chat("c", "hi", undefined, cfg);

    const [withToken, withoutToken] = fetchMock.mock.calls.map(
      ([, init]) => new Headers(init?.headers),
    );
    expect(withToken?.get("X-Hivegent-Principal")).toBe("service");
    expect(withoutToken?.has("X-Hivegent-Principal")).toBe(false);
  });
});
