import { beforeEach, expect, it, vi } from "vitest";

import type { getOidc as getOidcFn } from "@/oidc";

vi.mock("@/oidc", () => ({
  getOidc: vi
    .fn<typeof getOidcFn>()
    .mockResolvedValue({ isUserLoggedIn: false } as Awaited<ReturnType<typeof getOidcFn>>),
}));

vi.mock("@/lib/health", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/health")>()),
  waitForBackendReady: () => Promise.resolve(),
}));

import { chatFetch } from "@/api/client";
import { getDirectories, getDocumentContent } from "@/api/documents";
import { getSettings } from "@/api/settings";

const reply = (body: string, status = 200, headers?: HeadersInit) =>
  new Response(body, { status, headers });

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>());
});

it("reads a /tmp path from the conversation that owns it", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(reply("print(1)"));

  expect(await getDocumentContent("/tmp/my report.py", "c1")).toBe("print(1)");
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining("/api/v1/documents/%2Ftmp/my%20report.py?conversation_id=c1"),
    expect.anything(),
  );
});

it("parses settings and the directory tree a request answers", async () => {
  const user = {
    id: "user1",
    email: "user@test.com",
    name: "Test User",
    workspaces: [],
    roles: [],
  };
  const settings = {
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
    user,
    attachments: { media_types: ["image/jpeg"], max_bytes: 1024 },
    transparency: { enabled: true, contact_email: "a@example.com", minimum_watermark_tokens: 200 },
    ragold_url: null,
    debug: false,
  };
  const tree = {
    root: { type: "directory", name: "", path: "", children: [] },
    total_files: 0,
    total_directories: 0,
  };
  vi.mocked(fetch)
    .mockResolvedValueOnce(reply(JSON.stringify(settings)))
    .mockResolvedValueOnce(reply(JSON.stringify(tree)));

  expect((await getSettings()).models[0]?.reasoning).toEqual(["low"]);
  expect((await getDirectories("~")).root.type).toBe("directory");
});

it.each([
  [500, /^Failed to fetch directory tree \(HTTP 500\)$/],
  [413, /too large.*\(HTTP 413\)/],
  [503, /temporarily unavailable.*\(HTTP 503\)/],
])("explains an empty %i answer with its status", async (status, message) => {
  vi.mocked(fetch).mockResolvedValueOnce(reply("", status));

  await expect(getDirectories("~")).rejects.toThrow(message);
});

it("sends a rate-limited turn again once its Retry-After passed", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(reply("", 429, { "Retry-After": "2" }))
    .mockResolvedValueOnce(reply("ok"));

  const response = chatFetch("/chat", { method: "POST" });
  await vi.advanceTimersByTimeAsync(1999);
  expect(fetch).toHaveBeenCalledTimes(1);

  await vi.advanceTimersByTimeAsync(1);

  expect(await (await response).text()).toBe("ok");
  expect(fetch).toHaveBeenCalledTimes(2);
});
