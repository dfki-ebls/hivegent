import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { getOidc as getOidcFn } from "@/oidc";

// authFetch resolves auth headers via getOidc; without a stub it hangs on
// the missing oidc-spa bootstrap and the settings tests time out.
vi.mock("@/oidc", () => ({
  getOidc: vi
    .fn<typeof getOidcFn>()
    .mockResolvedValue({ isUserLoggedIn: false } as Awaited<ReturnType<typeof getOidcFn>>),
}));

// buildLlmConfig zeroes out its payload when the llmSpec flag is off, so
// enable it to exercise the field-mapping logic.
vi.mock("@/lib/feature-flags", () => ({
  featureFlags: {
    llmSpec: true,
    pipelineSpec: false,
    assetSpec: false,
    toolsSpec: false,
    agentModes: false,
  },
}));

import { buildLlmConfig, getDirectories, getSettings, requiresConversion } from "@/lib/api";
import { waitForBackendReady } from "@/lib/health";

// authFetch gates every request on backend readiness; resolve the cached probe
// once so each test's mocked fetch serves the endpoint, not the health poll.
beforeAll(async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 200 })));
  await waitForBackendReady();
});

describe("requiresConversion", () => {
  it("returns false for .md files", () => {
    expect(requiresConversion("report.md")).toBe(false);
  });

  it("returns true for non-markdown files", () => {
    expect(requiresConversion("document.pdf")).toBe(true);
  });
});

describe("buildLlmConfig", () => {
  it("returns empty config for empty input", () => {
    expect(buildLlmConfig({})).toEqual({});
  });

  it("maps model field", () => {
    expect(buildLlmConfig({ model: "gpt-4" })).toEqual({ model: "gpt-4" });
  });

  it("maps apiKey to api_key", () => {
    expect(buildLlmConfig({ apiKey: "sk-123" })).toEqual({
      api_key: "sk-123",
    });
  });

  it("maps baseUrl to base_url", () => {
    expect(buildLlmConfig({ baseUrl: "https://api.example.com" })).toEqual({
      base_url: "https://api.example.com",
    });
  });

  it("skips empty strings", () => {
    expect(buildLlmConfig({ model: "", apiKey: "" })).toEqual({});
  });
});

describe("getSettings", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("fetches and parses settings", async () => {
    const mockSettings = {
      model: "gpt-4",
      aux_model: "gpt-4o-mini",
      stt_model: "whisper-1",
      has_api_key: true,
      base_url: "https://api.openai.com",
      user: {
        id: "user1",
        email: "user@test.com",
        name: "Test User",
        read_groups: [],
        write_groups: [],
        roles: [],
      },
    };

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockSettings), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await getSettings();
    expect(result.model).toBe("gpt-4");
    expect(result.user.id).toBe("user1");
  });

  it("throws on non-ok response", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 500 }));

    await expect(getSettings()).rejects.toThrow("Failed to fetch settings");
  });
});

describe("getDirectories", () => {
  const tree = JSON.stringify({
    root: { type: "directory", name: "", path: "", children: [] },
    total_files: 0,
    total_directories: 0,
  });

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("fetches and parses the tree", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(tree, { status: 200 }));

    const result = await getDirectories("~");
    expect(result.root.type).toBe("directory");
  });

  it("includes the HTTP status in the error message", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 500 }));

    await expect(getDirectories("~")).rejects.toThrow("Failed to fetch directory tree (HTTP 500)");
  });

  it("explains opaque edge statuses instead of the bare fallback", async () => {
    // 413/429/5xx arrive from the proxy without a JSON detail body, so the
    // fallback maps them to a human explanation while keeping the status.
    vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 413 }));
    await expect(getDirectories("~")).rejects.toThrow(/too large.*\(HTTP 413\)/);

    vi.mocked(fetch).mockResolvedValueOnce(new Response("", { status: 503 }));
    await expect(getDirectories("~")).rejects.toThrow(/temporarily unavailable.*\(HTTP 503\)/);
  });
});
