import { beforeEach, describe, expect, it, vi } from "vitest";

// buildLlmConfig zeroes out its payload when the llmSpec flag is off, so
// enable it to exercise the field-mapping logic.
vi.mock("@/lib/feature-flags", () => ({
  featureFlags: { llmSpec: true, pipelineSpec: false, toolsSpec: false, planning: false },
}));

import { buildLlmConfig, getSettings, requiresConversion } from "@/lib/api";

describe("requiresConversion", () => {
  it("returns false for .md files", () => {
    expect(requiresConversion("report.md")).toBe(false);
  });

  it("returns true for .pdf files", () => {
    expect(requiresConversion("document.pdf")).toBe(true);
  });

  it("returns true for .docx files", () => {
    expect(requiresConversion("file.docx")).toBe(true);
  });

  it("returns true for .txt files", () => {
    expect(requiresConversion("notes.txt")).toBe(true);
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
