import { describe, expect, it } from "vitest";

import { previewKind } from "@/lib/original-preview";

describe("previewKind", () => {
  it("picks a renderer by media type, else by extension", () => {
    expect(previewKind("image/svg+xml", "logo.svg")).toBe("media");
    expect(previewKind("video/mp4", null)).toBe("media");
    expect(previewKind("application/pdf", "report")).toBe("pdf");
    expect(previewKind(null, "notes.DOCX")).toBe("docx");
    expect(previewKind("application/octet-stream", "table.xlsx")).toBe("xlsx");
  });

  it("leaves other files without a preview", () => {
    expect(previewKind("text/markdown", "notes.md")).toBeNull();
    expect(previewKind(null, "slides.pptx")).toBeNull();
    expect(previewKind(null, null)).toBeNull();
  });
});
