import { describe, expect, it, vi } from "vitest";

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

describe("syncWebFetchOutput", () => {
  const page = {
    url: "https://example.com/final",
    title: "Test",
    content: "# Hello",
    truncated: false,
  };

  it("stores the page under the final and requested URLs", () => {
    const markFullDocument = vi.fn<SyncOutputContext["markFullDocument"]>();
    syncWebFetchOutput(
      context({ input: { url: "https://example.com/start" }, metadata: page, markFullDocument }),
    );
    expect(markFullDocument).toHaveBeenCalledWith(
      "https://example.com/final",
      "# Hello",
      "web",
      undefined,
    );
    expect(markFullDocument).toHaveBeenCalledWith(
      "https://example.com/start",
      "# Hello",
      "web",
      undefined,
    );
  });

  it("keeps the capturing tool call on the stored page", () => {
    const markFullDocument = vi.fn<SyncOutputContext["markFullDocument"]>();
    syncWebFetchOutput(
      context({ input: { url: page.url }, metadata: page, markFullDocument, sourceId: "call-1" }),
    );
    expect(markFullDocument).toHaveBeenCalledWith(page.url, "# Hello", "web", "call-1");
  });

  it("ignores missing or empty structured payloads", () => {
    const markFullDocument = vi.fn<SyncOutputContext["markFullDocument"]>();
    syncWebFetchOutput(context({ input: { url: "https://x" }, metadata: null, markFullDocument }));
    syncWebFetchOutput(
      context({ input: { url: "https://x" }, metadata: { ...page, content: "" }, markFullDocument }),
    );
    expect(markFullDocument).not.toHaveBeenCalled();
  });
});

describe("syncWebSearchOutput", () => {
  it("adds one chunk per result keyed by URL", () => {
    const addChunk = vi.fn<SyncOutputContext["addChunk"]>();
    syncWebSearchOutput(
      context({
        input: { query: "jura e7" },
        metadata: [
          { title: "Manual", href: "https://example.com/manual", body: "snippet" },
          { title: "No link", href: "", body: "x" },
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
