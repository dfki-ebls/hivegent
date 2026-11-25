import Fuse from "fuse.js";
import { useMemo } from "react";

const KEY_SEPARATOR = "\0";

/**
 * Fuzzy-filter `items` by `query` over the given object keys.
 *
 * A blank query passes `items` through untouched without building an index,
 * which is then rebuilt only when `items` change, not on every keystroke.
 */
export function useFuzzySearch<T>(
  items: readonly T[],
  query: string,
  ...keys: (keyof T & string)[]
): readonly T[] {
  // Joined so callers can pass keys inline, without a stable array identity.
  const keyList = keys.join(KEY_SEPARATOR);
  const isSearching = query.trim() !== "";
  // Explicit, since the React Compiler takes a search for a mutation and would rebuild the index with it.
  const fuse = useMemo(
    () =>
      isSearching ? new Fuse(items, { keys: keyList.split(KEY_SEPARATOR), threshold: 0.4 }) : null,
    [items, keyList, isSearching],
  );

  return fuse ? fuse.search(query.trim()).map((result) => result.item) : items;
}
