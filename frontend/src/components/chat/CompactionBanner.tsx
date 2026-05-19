import { HistoryIcon, Minimize2 } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface CompactionBannerProps {
  compactedFrom: string | null;
  isCompacting: boolean;
  onNavigatePrevious: (previousId: string) => void;
}

export function CompactionBanner({
  compactedFrom,
  isCompacting,
  onNavigatePrevious,
}: CompactionBannerProps) {
  return (
    <>
      {compactedFrom && (
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
      )}
      {isCompacting && (
        <Alert>
          <Minimize2 className="h-4 w-4" />
          <AlertTitle>Compacting conversation</AlertTitle>
          <AlertDescription>
            Summarizing the conversation to fit within context limits...
          </AlertDescription>
        </Alert>
      )}
    </>
  );
}
