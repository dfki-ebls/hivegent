import { HistoryIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MarkdownText } from "@/components/chat/markdown/MarkdownText";
import { CollapsibleTrigger } from "@/components/ui/collapsible";
import { Marker, MarkerContent, MarkerIcon } from "@/components/ui/marker";
import { TraceCollapsible, TraceCollapsibleContent } from "@/components/ui/trace";

/** The divider where a summary replaced the conversation before it, which opens to that summary. */
export function CompactionPart({ content }: { content: string }) {
  const { t } = useTranslation();

  return (
    <TraceCollapsible>
      <Marker
        variant="separator"
        className="cursor-pointer hover:text-foreground"
        render={<CollapsibleTrigger />}
      >
        <MarkerIcon>
          <HistoryIcon />
        </MarkerIcon>
        <MarkerContent>{t(($) => $.chat.compaction.compacted)}</MarkerContent>
      </Marker>
      <TraceCollapsibleContent>
        <MarkdownText>{content}</MarkdownText>
      </TraceCollapsibleContent>
    </TraceCollapsible>
  );
}
