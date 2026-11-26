import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useProgressive } from "@/hooks/use-progressive";

describe("useProgressive", () => {
  it("shows the first step at once and the rest over later frames", async () => {
    const items = Array.from({ length: 5 }, (_, i) => i);
    const { result } = renderHook(() => useProgressive(items, 2));

    expect(result.current).toEqual([0, 1]);

    await waitFor(() => expect(result.current).toBe(items));
  });
});
