import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { cn, offsetInScroller } from "@/lib/utils";

interface VirtualListProps<T> {
  items: readonly T[];
  /** A stable key per item, which keeps its measured size while the items around it change. */
  getKey: (item: T) => string;
  children: (item: T) => ReactNode;
  /** The element scrolling the list, which may hold more than the list, such as a header above it. */
  scrollElement: HTMLElement | null;
  /** The height of an item in pixels until it is measured. */
  estimateSize: number;
  /** The space between items in pixels. */
  gap?: number;
  /** The index of an item to keep mounted out of view, such as the one holding focus. */
  pinned?: number;
  className?: string;
}

/**
 * A list that mounts only the items in and near view, each measured once it renders,
 * so a list of thousands of items mounts and changes as fast as a short one.
 */
export function VirtualList<T>({
  items,
  getKey,
  children,
  scrollElement,
  estimateSize,
  gap = 0,
  pinned,
  className,
}: VirtualListProps<T>) {
  const list = useRef<HTMLDivElement>(null);
  const [scrollMargin, setScrollMargin] = useState(0);
  // The React Compiler skips this component, as the virtualizer is a mutable instance,
  // and nothing it returns reaches a memoized child.
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollElement,
    estimateSize: () => estimateSize,
    getItemKey: (index) => {
      const item = items[index];

      return item === undefined ? index : getKey(item);
    },
    gap,
    scrollMargin,
    rangeExtractor: (range) => {
      const indexes = defaultRangeExtractor(range);

      return pinned === undefined || items[pinned] === undefined || indexes.includes(pinned)
        ? indexes
        : [...indexes, pinned].toSorted((a, b) => a - b);
    },
  });

  // Whatever sits above the list in the scroll element, such as a header, offsets its items,
  // and any change of it resizes a child of the scroll element.
  useEffect(() => {
    const element = list.current;

    if (!element || !scrollElement) return;

    const observer = new ResizeObserver(() =>
      setScrollMargin(offsetInScroller(element, scrollElement)),
    );
    for (const child of scrollElement.children) observer.observe(child);

    return () => observer.disconnect();
  }, [scrollElement]);

  return (
    <div
      ref={list}
      className={cn("relative", className)}
      style={{ height: virtualizer.getTotalSize() }}
    >
      {virtualizer.getVirtualItems().map(({ index, key, start }) => {
        const item = items[index];

        return (
          item !== undefined && (
            <div
              key={key}
              ref={virtualizer.measureElement}
              data-index={index}
              className="absolute inset-x-0 top-0"
              style={{ transform: `translateY(${start - scrollMargin}px)` }}
            >
              {children(item)}
            </div>
          )
        );
      })}
    </div>
  );
}
