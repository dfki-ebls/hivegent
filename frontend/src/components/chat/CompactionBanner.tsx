import { HistoryIcon } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface CompactionBannerProps {
  compactedFrom: string | null;
  onNavigatePrevious: (previousId: string) => void;
}

// In-progress feedback lives in a toast (see use-compaction): it stays
// visible regardless of scroll position and is scoped to the conversation
// being compacted, unlike an inline banner driven by hook-instance state.
export function CompactionBanner({ compactedFrom, onNavigatePrevious }: CompactionBannerProps) {
  const { t } = useTranslation();

  if (!compactedFrom) return null;

  return (
    <Alert>
      <HistoryIcon className="h-4 w-4" />
      <AlertTitle>{t(($) => $.chat.compaction.bannerTitle)}</AlertTitle>
      <AlertDescription>
        <p>
          <Trans
            i18nKey={($) => $.chat.compaction.bannerText}
            components={{
              previous: (
                // The translated sentence supplies the button's text.
                // oxlint-disable-next-line jsx-a11y/control-has-associated-label
                <button
                  type="button"
                  onClick={() => onNavigatePrevious(compactedFrom)}
                  className="underline hover:text-primary"
                />
              ),
            }}
          />
        </p>
      </AlertDescription>
    </Alert>
  );
}
