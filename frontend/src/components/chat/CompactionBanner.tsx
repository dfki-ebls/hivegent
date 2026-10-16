import { HistoryIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker";

interface CompactionBannerProps {
  compactedFrom: string;
  onNavigatePrevious: (previousId: string) => void;
}

/**
 * The divider above a conversation compacted from another, which opens the previous one.
 * In-progress feedback lives in a toast (see `use-compaction`), which stays visible whatever the scroll position.
 */
export function CompactionBanner({ compactedFrom, onNavigatePrevious }: CompactionBannerProps) {
  const { t } = useTranslation();
  const label = t(($) => $.chat.compaction.continued);

  return (
    <Marker
      variant="separator"
      className="hover:text-foreground"
      render={
        <button
          type="button"
          aria-label={label}
          onClick={() => onNavigatePrevious(compactedFrom)}
        />
      }
    >
      <MarkerIcon>
        <HistoryIcon />
      </MarkerIcon>
      <MarkerContent>{label}</MarkerContent>
    </Marker>
  );
}
