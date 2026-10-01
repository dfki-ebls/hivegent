import { useEffect, useState } from "react";

type ObjectUrlFetcher = (signal: AbortSignal) => Promise<string>;

interface ObjectUrlResult {
  fetch: ObjectUrlFetcher;
  url: string | null;
  error: boolean;
}

/**
 * Fetch a resource as an object URL, aborting the request and revoking the URL
 * on unmount or when the fetcher changes.
 *
 * The fetcher receives an `AbortSignal` and must return a freshly created
 * object URL (e.g. from `URL.createObjectURL`); the hook owns its lifetime.
 * Memoize the fetcher with `useCallback` so it only re-runs when its inputs
 * change. Pass `null` to skip fetching — combine with {@link useInView} to defer
 * loading until the target is on screen.
 */
export function useObjectUrl(fetch: ObjectUrlFetcher | null): {
  url: string | null;
  error: boolean;
} {
  // Tagged with the fetcher it came from, so a new fetcher reads as pending
  // without resetting state inside the effect.
  const [result, setResult] = useState<ObjectUrlResult | null>(null);

  useEffect(() => {
    if (!fetch) return;

    const controller = new AbortController();
    let created: string | null = null;

    fetch(controller.signal)
      .then((objectUrl) => {
        // Aborts can land after the blob resolves; revoke instead of leaking.
        if (controller.signal.aborted) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        created = objectUrl;
        setResult({ fetch, url: objectUrl, error: false });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResult({ fetch, url: null, error: true });
      });

    return () => {
      controller.abort();
      if (created) URL.revokeObjectURL(created);
    };
  }, [fetch]);

  const current = result?.fetch === fetch ? result : null;

  return { url: current?.url ?? null, error: current?.error ?? false };
}
