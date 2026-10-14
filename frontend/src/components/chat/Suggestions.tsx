import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.suggestions);

/** Catalog ids of the starter prompts, each a short label plus the detailed prompt it sends. */
const SUGGESTIONS = ["overview", "actionItems", "deadlines", "decisions", "risks"] as const;

interface ChatSuggestionsProps {
  onSelect: (prompt: string) => void;
}

export function ChatSuggestions({ onSelect }: ChatSuggestionsProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <div className="flex flex-wrap gap-2">
      {SUGGESTIONS.map((id) => (
        <Button
          key={id}
          variant="outline"
          size="sm"
          onClick={() => onSelect(t(($) => $[id].prompt))}
        >
          {t(($) => $[id].label)}
        </Button>
      ))}
    </div>
  );
}
