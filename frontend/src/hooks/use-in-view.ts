import { useEffect, useState } from "react";

/**
 * Latch to `true` the first time the referenced element scrolls within
 * `rootMargin` of the viewport, then stop observing.
 *
 * Defers expensive per-element work (such as image fetches) until the element
 * is about to be seen, so a long document with hundreds of assets loads only
 * what is on screen instead of everything at once. Attach the returned ref to a
 * stable element that stays mounted across the loading transition.
 */
export function useInView(rootMargin = "256px"): [(node: Element | null) => void, boolean] {
  const [node, setNode] = useState<Element | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (!node || inView) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setInView(true);
      },
      { rootMargin },
    );
    observer.observe(node);

    return () => observer.disconnect();
  }, [node, inView, rootMargin]);

  return [setNode, inView];
}
