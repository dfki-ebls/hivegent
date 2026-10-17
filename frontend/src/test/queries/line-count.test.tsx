import { useQuery } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { getDocumentLineCounts } from "@/lib/api";
import { documentQueries } from "@/queries/documents";
import { createQueryWrapper } from "@/test/render";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getDocumentLineCounts: vi.fn<typeof getDocumentLineCounts>(),
}));

describe("documentQueries.lineCount", () => {
  it("batches the lookups rendered together into one request", async () => {
    vi.mocked(getDocumentLineCounts).mockResolvedValue({ "a.md": 40 });

    const { result } = renderHook(
      () => [
        useQuery(documentQueries.lineCount("a.md")).data,
        useQuery(documentQueries.lineCount("tmp.md")).data,
      ],
      { wrapper: createQueryWrapper() },
    );

    await waitFor(() => expect(result.current).toEqual([40, null]));
    expect(getDocumentLineCounts).toHaveBeenCalledExactlyOnceWith(["a.md", "tmp.md"]);
  });
});
