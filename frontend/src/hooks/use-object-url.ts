import { useEffect, useState } from "react";

/**
 * Fetch a resource as an object URL, revoking it on unmount or when the
 * fetcher changes.
 *
 * The fetcher must return a freshly created object URL (e.g. from
 * `URL.createObjectURL`); the hook owns its lifetime. Memoize the fetcher
 * with `useCallback` so it only re-runs when its inputs change. Pass `null`
 * to skip fetching.
 */
export function useObjectUrl(fetch: (() => Promise<string>) | null): {
  url: string | null;
  error: boolean;
} {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setUrl(null);
    setError(false);
    if (!fetch) return;

    let active = true;
    let created: string | null = null;

    fetch()
      .then((u) => {
        if (active) {
          created = u;
          setUrl(u);
        } else {
          URL.revokeObjectURL(u);
        }
      })
      .catch(() => {
        if (active) setError(true);
      });

    return () => {
      active = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [fetch]);

  return { url, error };
}
