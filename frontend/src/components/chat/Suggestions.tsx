import { useTranslation } from "react-i18next";
import { Suggestion, Suggestions as SuggestionsRoot } from "@/components/ai-elements/suggestion";
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
    <SuggestionsRoot className="flex-wrap">
      {SUGGESTIONS.map((id) => (
        <Suggestion key={id} suggestion={t(($) => $[id].prompt)} onClick={onSelect}>
          {t(($) => $[id].label)}
        </Suggestion>
      ))}
    </SuggestionsRoot>
  );
}
