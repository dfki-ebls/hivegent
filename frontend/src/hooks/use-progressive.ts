import { startTransition, useEffect, useState } from "react";

/**
 * The leading *step* items of *items* at first, growing by *step* every frame until all show,
 * so a long list mounts in slices that leave the page responsive instead of in one long task.
 */
export function useProgressive<T>(items: readonly T[], step = 1): readonly T[] {
  const [count, setCount] = useState(step);
  const done = count >= items.length;

  useEffect(() => {
    if (done) return;

    const frame = requestAnimationFrame(() => startTransition(() => setCount((n) => n + step)));

    return () => cancelAnimationFrame(frame);
  }, [count, done, step]);

  return done ? items : items.slice(0, count);
}
