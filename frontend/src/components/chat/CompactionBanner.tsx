import { HistoryIcon } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface CompactionBannerProps {
  compactedFrom: string | null;
  onNavigatePrevious: (previousId: string) => void;
}

// In-progress feedback lives in a toast (see use-auto-compact): it stays
// visible regardless of scroll position and is scoped to the conversation
// being compacted, unlike an inline banner driven by hook-instance state.
export function CompactionBanner({ compactedFrom, onNavigatePrevious }: CompactionBannerProps) {
  if (!compactedFrom) return null;

  return (
    <Alert>
      <HistoryIcon className="h-4 w-4" />
      <AlertTitle>Continued conversation</AlertTitle>
      <AlertDescription>
        <p>
          This conversation was compacted from a{" "}
          <button
            type="button"
            onClick={() => onNavigatePrevious(compactedFrom)}
            className="underline hover:text-primary"
          >
            previous chat
          </button>
          .
        </p>
      </AlertDescription>
    </Alert>
  );
}
