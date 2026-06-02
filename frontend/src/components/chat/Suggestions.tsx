import { Suggestion, Suggestions as SuggestionsRoot } from "@/components/ai-elements/suggestion";

interface SuggestedPrompt {
  /** Short text shown on the button. */
  label: string;
  /** Detailed prompt sent to the model when the button is clicked. */
  prompt: string;
}

const SUGGESTED_PROMPTS: SuggestedPrompt[] = [
  {
    label: "Workspace overview",
    prompt:
      "Give me a high-level overview of my document collection. List the main folders or categories and briefly describe the kinds of business documents in each.",
  },
  {
    label: "Open action items",
    prompt:
      "Search across my documents for open action items, tasks, and follow-ups. List each one with who is responsible and any mentioned deadline.",
  },
  {
    label: "Upcoming deadlines",
    prompt:
      "Find the key dates and deadlines mentioned across my documents and list them in chronological order, noting which document each one comes from.",
  },
  {
    label: "Key decisions",
    prompt:
      "Identify the important decisions recorded across my documents. For each one, summarize what was decided and reference the document and date.",
  },
  {
    label: "Risks & blockers",
    prompt:
      "Search my documents for any risks, blockers, or open issues that have been raised, and summarize them grouped by topic.",
  },
];

interface ChatSuggestionsProps {
  onSelect: (prompt: string) => void;
}

export function ChatSuggestions({ onSelect }: ChatSuggestionsProps) {
  return (
    <SuggestionsRoot className="flex-wrap">
      {SUGGESTED_PROMPTS.map(({ label, prompt }) => (
        <Suggestion key={label} suggestion={prompt} onClick={onSelect}>
          {label}
        </Suggestion>
      ))}
    </SuggestionsRoot>
  );
}
