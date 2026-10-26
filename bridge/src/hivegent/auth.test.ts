import { afterEach, describe, expect, it, vi } from "vitest";

import { ServiceTokenProvider } from "./auth.js";

const config = { issuer: "https://sso.example.com", clientId: "bot", clientSecret: "secret" };

function stubDiscovery(doc: object): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(doc)),
  );
}

describe("ServiceTokenProvider", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("rejects a discovery document for another issuer", async () => {
    stubDiscovery({
      issuer: "https://evil.example.com",
      token_endpoint: "https://evil.example.com/token",
    });
    await expect(new ServiceTokenProvider(config).getToken()).rejects.toThrow("does not match");
  });

  it("refuses to send the secret to a plain HTTP token endpoint", async () => {
    stubDiscovery({
      issuer: "https://sso.example.com/",
      token_endpoint: "http://sso.example.com/token",
    });
    await expect(new ServiceTokenProvider(config).getToken()).rejects.toThrow("insecure");
  });
});
