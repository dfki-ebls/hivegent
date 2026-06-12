import { useEffect, useState } from "react";

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
export function useObjectUrl(fetch: ((signal: AbortSignal) => Promise<string>) | null): {
  url: string | null;
  error: boolean;
} {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setUrl(null);
    setError(false);
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
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });

    return () => {
      controller.abort();
      if (created) URL.revokeObjectURL(created);
    };
  }, [fetch]);

  return { url, error };
}
