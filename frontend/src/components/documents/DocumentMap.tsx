import { cn } from "@/lib/utils";
import type { MapSegment } from "./utils";

interface DocumentMapProps {
  segments: MapSegment[];
  className?: string;
}

/** Coverage bar showing where a document's read chunks sit within the whole. */
export function DocumentMap({ segments, className }: DocumentMapProps) {
  if (segments.length === 0) return null;

  return (
    <div
      aria-hidden
      className={cn(
        "relative ml-auto h-1.5 w-2/5 max-w-40 shrink-0 overflow-hidden rounded-full bg-muted",
        className,
      )}
    >
      {segments.map((seg, i) => (
        <div
          key={i}
          className="absolute inset-y-0 rounded-full bg-emerald-500"
          style={{
            left: `${seg.start * 100}%`,
            width: `${Math.max((seg.end - seg.start) * 100, 2)}%`,
          }}
        />
      ))}
    </div>
  );
}
