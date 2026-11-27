import { renderHook } from "@testing-library/react";
import { expect, it } from "vitest";

import { useProgressive } from "@/hooks/use-progressive";
import { FRAME_MS, settle } from "@/test/render";

it("shows the first step at once and the rest over later frames", async () => {
  const items = [0, 1, 2, 3, 4];
  const { result } = renderHook(() => useProgressive(items, 2));

  expect(result.current).toEqual([0, 1]);

  await settle(FRAME_MS);
  expect(result.current).toEqual([0, 1, 2, 3]);

  await settle(FRAME_MS);
  expect(result.current).toBe(items);
});
