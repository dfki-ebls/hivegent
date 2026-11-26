import { vi } from "vitest";

/**
 * Lays out what jsdom cannot, so a virtual list mounts its items:
 * every element is *height* pixels tall and nothing ever resizes.
 */
export function stubLayout(height = 600): void {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(height);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
}
