import { describe, expect, it, vi } from "vitest";

import { fetchRetryable, HttpError, isTransient, NetworkError, retryTransient } from "./retry.js";

describe("isTransient", () => {
  it("retries what passes once the peer is up and nothing else", () => {
    expect(isTransient(new HttpError(503, "starting"))).toBe(true);
    expect(isTransient(new HttpError(429, "busy"))).toBe(true);
    expect(isTransient(new NetworkError("fetch failed"))).toBe(true);
    expect(isTransient(new DOMException("slow", "TimeoutError"))).toBe(true);
    expect(isTransient(new TypeError("undefined is not a function"))).toBe(false);
    expect(isTransient(new HttpError(401, "unauthorized"))).toBe(false);
    expect(isTransient(new Error("authenticates the bridge as a person"))).toBe(false);
  });
});

describe("fetchRetryable", () => {
  it("turns a failed request into a network error", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });

    await expect(fetchRetryable("http://localhost")).rejects.toBeInstanceOf(NetworkError);
    vi.unstubAllGlobals();
  });
});

describe("retryTransient", () => {
  it("gives up on a permanent failure at once", async () => {
    const attempt = vi.fn<() => Promise<void>>(async () => {
      throw new HttpError(403, "forbidden");
    });

    await expect(retryTransient("check", attempt)).rejects.toThrow("forbidden");
    expect(attempt).toHaveBeenCalledOnce();
  });
});
