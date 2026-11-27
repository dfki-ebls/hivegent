import { expect, it, vi } from "vitest";

import { syncReadDocumentOutput } from "@/components/chat/tools/read-document";
import { syncWebFetchOutput } from "@/components/chat/tools/web-fetch";
import { syncWebSearchOutput } from "@/components/chat/tools/web-search";
import type { SyncOutputContext } from "@/lib/chat/tool-part";

function context(overrides: Partial<SyncOutputContext>): SyncOutputContext {
  return {
    input: undefined,
    text: null,
    metadata: null,
    addChunk: vi.fn<SyncOutputContext["addChunk"]>(),
    markFullDocument: vi.fn<SyncOutputContext["markFullDocument"]>(),
    addImage: vi.fn<SyncOutputContext["addImage"]>(),
    ...overrides,
  };
}

const failure = { item: "https://x", reason: "HTTP 403.", kind: "failure" };

it("stores each fetched page under its final and requested URLs, and none that is empty", () => {
  const page = {
    requested_url: "https://example.com/start",
    url: "https://example.com/final",
    title: "Test",
    start_line: 1,
    end_line: 1,
    total_lines: 1,
    content: "# Hello",
    truncated: false,
  };
  const ctx = context({ metadata: [page, { ...page, content: "" }, failure], sourceId: "c1" });

  syncWebFetchOutput(ctx);

  expect(vi.mocked(ctx.markFullDocument).mock.calls).toEqual([
    ["https://example.com/final", "# Hello", "web", "c1", 1],
    ["https://example.com/start", "# Hello", "web", "c1", 1],
  ]);
});

it("adds one chunk per search result with a link, detailed by its own query", () => {
  const hits = [
    { title: "Manual", href: "https://example.com/manual", body: "snippet" },
    { title: "No link", href: "", body: "x" },
  ];
  const ctx = context({ metadata: [{ query: "jura e7", hits }, failure] });

  syncWebSearchOutput(ctx);

  expect(ctx.addChunk).toHaveBeenCalledExactlyOnceWith({
    filename: "https://example.com/manual",
    content: "snippet",
    origin: "web",
    detail: "jura e7",
    position: { type: "web_result", url: "https://example.com/manual" },
  });
});

it("stores a whole read as a document and a partial one as a chunk of its own path", () => {
  const range = { start_line: 1, end_line: 2, total_lines: 2, content: "a\nb", content_hash: "h" };
  const ctx = context({
    metadata: [
      { ...range, file_path: "~/a.md" },
      { ...range, file_path: "~/b.md", total_lines: 9 },
      { item: "~/c.md", reason: "'~/c.md' not found.", kind: "failure" },
    ],
  });

  syncReadDocumentOutput(ctx);

  expect(vi.mocked(ctx.markFullDocument).mock.calls).toEqual([
    ["~/a.md", "a\nb", "read", undefined, 2],
  ]);
  expect(ctx.addChunk).toHaveBeenCalledExactlyOnceWith(
    {
      filename: "~/b.md",
      content: "a\nb",
      origin: "read",
      position: { type: "line_range", startLine: 1, endLine: 2 },
      sourceId: undefined,
    },
    9,
  );
});
