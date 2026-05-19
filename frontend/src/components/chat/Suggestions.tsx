import { Suggestion, Suggestions as SuggestionsRoot } from "@/components/ai-elements/suggestion";

const SUGGESTED_QUESTIONS = [
  "What documents do I have?",
  "Summarize my most recent notes",
  "Find documents about meetings",
  "What are my action items?",
];

interface ChatSuggestionsProps {
  onSelect: (question: string) => void;
}

export function ChatSuggestions({ onSelect }: ChatSuggestionsProps) {
  return (
    <SuggestionsRoot className="flex-wrap">
      {SUGGESTED_QUESTIONS.map((question) => (
        <Suggestion key={question} suggestion={question} onClick={onSelect} />
      ))}
    </SuggestionsRoot>
  );
}
