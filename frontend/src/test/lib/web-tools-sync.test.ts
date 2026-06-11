import { describe, expect, it, vi } from "vitest";

import { syncWebFetchOutput } from "@/components/chat/tools/web-fetch";
import { syncWebSearchOutput } from "@/components/chat/tools/web-search";

const noop = () => {};

describe("syncWebFetchOutput", () => {
  const page = {
    url: "https://example.com/final",
    title: "Test",
    content: "# Hello",
    truncated: false,
  };

  it("stores the page under the final and requested URLs", () => {
    const markFullDocument = vi.fn<() => void>();
    syncWebFetchOutput(
      { url: "https://example.com/start" },
      "ignored",
      page,
      noop,
      markFullDocument,
      noop,
    );
    expect(markFullDocument).toHaveBeenCalledWith(
      "https://example.com/final",
      "# Hello",
      "web_fetch",
    );
    expect(markFullDocument).toHaveBeenCalledWith(
      "https://example.com/start",
      "# Hello",
      "web_fetch",
    );
  });

  it("ignores missing or empty structured payloads", () => {
    const markFullDocument = vi.fn<() => void>();
    syncWebFetchOutput({ url: "https://x" }, "text", null, noop, markFullDocument, noop);
    syncWebFetchOutput(
      { url: "https://x" },
      null,
      { ...page, content: "" },
      noop,
      markFullDocument,
      noop,
    );
    expect(markFullDocument).not.toHaveBeenCalled();
  });
});

describe("syncWebSearchOutput", () => {
  it("adds one chunk per result keyed by URL", () => {
    const addChunk = vi.fn<() => void>();
    syncWebSearchOutput(
      { query: "jura e7" },
      null,
      [
        { title: "Manual", href: "https://example.com/manual", body: "snippet" },
        { title: "No link", href: "", body: "x" },
      ],
      addChunk,
      noop,
      noop,
    );
    expect(addChunk).toHaveBeenCalledTimes(1);
    expect(addChunk).toHaveBeenCalledWith({
      filename: "https://example.com/manual",
      content: "snippet",
      source: "web: jura e7",
      position: { type: "web_result", url: "https://example.com/manual" },
    });
  });
});
