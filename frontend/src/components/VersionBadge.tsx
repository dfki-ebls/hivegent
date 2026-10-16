import { Badge } from "@/components/ui/badge";

/** The release stage, the same in every language. */
const RELEASE_STAGE = "Alpha";

/** A subtle marker noting the app's release stage, shown beside the wordmark. */
export function VersionBadge({ className }: { className?: string }) {
  return (
    <Badge variant="outline" className={className}>
      {RELEASE_STAGE}
    </Badge>
  );
}
