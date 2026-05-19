import { BrainIcon } from "lucide-react";
import {
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSelectValue,
} from "@/components/ai-elements/prompt-input";
import { REASONING_EFFORT_OPTIONS, type ReasoningEffort } from "@/lib/types";

interface ReasoningEffortSelectorProps {
  value: ReasoningEffort;
  onChange: (value: ReasoningEffort) => void;
}

export function ReasoningEffortSelector({ value, onChange }: ReasoningEffortSelectorProps) {
  return (
    <PromptInputSelect value={value} onValueChange={(v) => onChange(v as ReasoningEffort)}>
      <PromptInputSelectTrigger className="h-8 w-auto min-w-20">
        <BrainIcon className="h-4 w-4" />
        <PromptInputSelectValue placeholder="Effort" />
      </PromptInputSelectTrigger>
      <PromptInputSelectContent>
        {REASONING_EFFORT_OPTIONS.map((option) => (
          <PromptInputSelectItem key={option.value} value={option.value}>
            {option.label}
          </PromptInputSelectItem>
        ))}
      </PromptInputSelectContent>
    </PromptInputSelect>
  );
}
