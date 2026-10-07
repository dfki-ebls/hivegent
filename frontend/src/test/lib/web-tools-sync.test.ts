import { describe, expect, it, vi } from "vitest";

import { syncReadDocumentOutput } from "@/components/chat/tools/read-document";
import { syncWebFetchOutput } from "@/components/chat/tools/web-fetch";
import { syncWebSearchOutput } from "@/components/chat/tools/web-search";
import type { SyncOutputContext } from "@/lib/chat/tool-part";

const noop = () => {};

function context(overrides: Partial<SyncOutputContext>): SyncOutputContext {
  return {
    input: undefined,
    text: null,
    metadata: null,
    addChunk: noop,
    markFullDocument: noop,
    addImage: noop,
    ...overrides,
  };
}

const failure = { item: "https://x", reason: "HTTP 403.", kind: "failure" };

describe("syncWebFetchOutput", () => {
  const page = {
    requested_url: "https://example.com/start",
    url: "https://example.com/final",
    title: "Test",
    content: "# Hello",
    truncated: false,
  };

  it("stores each page under its final and requested URLs", () => {
    const markFullDocument = vi.fn<SyncOutputContext["markFullDocument"]>();
    syncWebFetchOutput(context({ metadata: [page, failure], markFullDocument, sourceId: "c1" }));
    expect(markFullDocument.mock.calls).toEqual([
      ["https://example.com/final", "# Hello", "web", "c1"],
      ["https://example.com/start", "# Hello", "web", "c1"],
    ]);
  });

  it("ignores missing or empty structured payloads", () => {
    const markFullDocument = vi.fn<SyncOutputContext["markFullDocument"]>();
    syncWebFetchOutput(context({ metadata: null, markFullDocument }));
    syncWebFetchOutput(context({ metadata: [{ ...page, content: "" }], markFullDocument }));
    expect(markFullDocument).not.toHaveBeenCalled();
  });
});

describe("syncWebSearchOutput", () => {
  it("adds one chunk per result keyed by URL, detailed by its own query", () => {
    const addChunk = vi.fn<SyncOutputContext["addChunk"]>();
    syncWebSearchOutput(
      context({
        metadata: [
          {
            query: "jura e7",
            edition: "en",
            hits: [
              { title: "Manual", href: "https://example.com/manual", body: "snippet" },
              { title: "No link", href: "", body: "x" },
            ],
          },
          failure,
        ],
        addChunk,
      }),
    );
    expect(addChunk).toHaveBeenCalledTimes(1);
    expect(addChunk).toHaveBeenCalledWith({
      filename: "https://example.com/manual",
      content: "snippet",
      origin: "web",
      detail: "jura e7",
      position: { type: "web_result", url: "https://example.com/manual" },
    });
  });
});

describe("syncReadDocumentOutput", () => {
  it("stores each read under its own path", () => {
    const markFullDocument = vi.fn<SyncOutputContext["markFullDocument"]>();
    const addChunk = vi.fn<SyncOutputContext["addChunk"]>();
    const range = { start_line: 1, end_line: 2, total_lines: 2, content: "a\nb", content_hash: "h" };
    syncReadDocumentOutput(
      context({
        metadata: [
          { ...range, file_path: "~/a.md" },
          { ...range, file_path: "~/b.md", total_lines: 9 },
          { item: "~/c.md", reason: "'~/c.md' not found.", kind: "failure" },
        ],
        markFullDocument,
        addChunk,
      }),
    );
    expect(markFullDocument).toHaveBeenCalledWith("~/a.md", "a\nb", "read", undefined);
    expect(addChunk).toHaveBeenCalledWith(
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
});
