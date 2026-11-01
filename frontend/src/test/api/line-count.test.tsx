import { useQuery } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { documentQueries } from "@/api/documents";
import { createQueryWrapper } from "@/test/render";

const { requestJson } = vi.hoisted(() => ({
  requestJson: vi.fn<(url: string, error: string, schema: unknown, init?: RequestInit) => Promise<unknown>>(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  requestJson,
}));

describe("documentQueries.lineCount", () => {
  it("batches the lookups rendered together into one request", async () => {
    requestJson.mockResolvedValue({ line_counts: { "a.md": 40 } });

    const { result } = renderHook(
      () => [
        useQuery(documentQueries.lineCount("a.md")).data,
        useQuery(documentQueries.lineCount("tmp.md")).data,
      ],
      { wrapper: createQueryWrapper() },
    );

    await waitFor(() => expect(result.current).toEqual([40, null]));
    expect(requestJson).toHaveBeenCalledOnce();
    expect(requestJson.mock.calls[0]?.[3]?.body).toBe(JSON.stringify({ files: ["a.md", "tmp.md"] }));
  });
});
