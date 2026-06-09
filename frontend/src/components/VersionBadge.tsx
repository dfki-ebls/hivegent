import { cn } from "@/lib/utils";
import { Badge } from "./ui/badge";

/** A subtle marker noting the app's release stage; shown beside the wordmark. */
export function VersionBadge({ label = "Alpha", className }: { label?: string; className?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "px-1.5 py-0 text-[0.625rem] tracking-wider text-muted-foreground uppercase",
        className,
      )}
    >
      {label}
    </Badge>
  );
}
