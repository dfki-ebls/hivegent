import { ListChecksIcon } from "lucide-react";
import {
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSelectValue,
} from "@/components/ai-elements/prompt-input";
import { AGENT_MODE_OPTIONS, type AgentMode } from "@/lib/types";

interface ModeSelectorProps {
  value: AgentMode;
  onChange: (value: AgentMode) => void;
}

export function ModeSelector({ value, onChange }: ModeSelectorProps) {
  return (
    <PromptInputSelect value={value} onValueChange={(v) => onChange(v as AgentMode)}>
      <PromptInputSelectTrigger className="h-8 w-auto min-w-20">
        <ListChecksIcon className="h-4 w-4" />
        <PromptInputSelectValue placeholder="Mode" />
      </PromptInputSelectTrigger>
      <PromptInputSelectContent>
        {AGENT_MODE_OPTIONS.map((option) => (
          <PromptInputSelectItem key={option.value} value={option.value}>
            {option.label}
          </PromptInputSelectItem>
        ))}
      </PromptInputSelectContent>
    </PromptInputSelect>
  );
}
