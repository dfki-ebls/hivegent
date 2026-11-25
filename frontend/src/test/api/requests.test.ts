import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { getOidc as getOidcFn } from "@/oidc";

// authFetch resolves auth headers via getOidc; without a stub it hangs on
// the missing oidc-spa bootstrap and the settings tests time out.
vi.mock("@/oidc", () => ({
  getOidc: vi
    .fn<typeof getOidcFn>()
    .mockResolvedValue({ isUserLoggedIn: false } as Awaited<ReturnType<typeof getOidcFn>>),
}));

import { chatFetch } from "@/api/client";
import { getSettings } from "@/api/settings";
import { getDirectories, getDocumentContent } from "@/api/documents";
import { waitForBackendReady } from "@/lib/health";

// authFetch gates every request on backend readiness; resolve the cached probe
// once so each test's mocked fetch serves the endpoint, not the health poll.
beforeAll(async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 200 })));
  await waitForBackendReady();
});

describe("getDocumentContent", () => {
  it("reads a /tmp path from the conversation that owns it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("print(1)")));

    expect(await getDocumentContent("/tmp/my report.py", "c1")).toBe("print(1)");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/documents/%2Ftmp/my%20report.py?conversation_id=c1"),
      expect.anything(),
    );
  });
});

describe("getSettings", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("fetches and parses settings", async () => {
    const mockSettings = {
      models: [
        {
          id: "big",
          label: "Big",
          vision: true,
          max_images: 2,
          reasoning: ["low"],
          context_window: null,
        },
      ],
      default_model: "big",
      transcription: true,
      user: {
        id: "user1",
        email: "user@test.com",
        name: "Test User",
        workspaces: [],
        roles: [],
      },
      attachments: {
        media_types: ["image/jpeg", "image/png"],
        max_bytes: 10 * 1024 * 1024,
      },
      transparency: {
        enabled: true,
        contact_email: "operator@example.com",
        minimum_watermark_tokens: 200,
      },
      ragold_url: null,
      debug: false,
    };

    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(mockSettings), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await getSettings();
    expect(result.models[0]?.reasoning).toEqual(["low"]);
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

describe("chatFetch", () => {
  it("sends a rate-limited turn again once its Retry-After passed", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response("", { status: 429, headers: { "Retry-After": "0" } }))
        .mockResolvedValueOnce(new Response("ok")),
    );

    expect(await (await chatFetch("/chat", { method: "POST" })).text()).toBe("ok");
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
