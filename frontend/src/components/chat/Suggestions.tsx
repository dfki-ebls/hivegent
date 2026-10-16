import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.suggestions);

/** Catalog ids of the starter prompts, each a short label plus the detailed prompt it sends. */
const SUGGESTIONS = ["overview", "actionItems", "deadlines", "decisions", "risks"] as const;

/** A row of starter prompts above the composer, which scrolls sideways when they do not fit. */
export function ChatSuggestions({ onSelect }: { onSelect: (prompt: string) => void }) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <div className="flex scroll-fade-x scrollbar-none gap-2 overflow-x-auto overscroll-x-contain">
      {SUGGESTIONS.map((id) => (
        <Button
          key={id}
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={() => onSelect(t(($) => $[id].prompt))}
        >
          {t(($) => $[id].label)}
        </Button>
      ))}
    </div>
  );
}
