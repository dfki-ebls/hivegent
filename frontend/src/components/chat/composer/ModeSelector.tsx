import {
  CircleQuestionMarkIcon,
  EyeIcon,
  ListChecksIcon,
  PencilIcon,
  type LucideIcon,
} from "lucide-react";
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

const MODE_ICONS: Record<AgentMode, LucideIcon> = {
  interactive: CircleQuestionMarkIcon,
  read: EyeIcon,
  write: PencilIcon,
  plan: ListChecksIcon,
};

export function ModeSelector({ value, onChange }: ModeSelectorProps) {
  const ActiveIcon = MODE_ICONS[value];

  return (
    <PromptInputSelect value={value} onValueChange={(v) => onChange(v as AgentMode)}>
      <PromptInputSelectTrigger className="h-8 w-auto min-w-20">
        <ActiveIcon className="h-4 w-4" />
        <PromptInputSelectValue placeholder="Mode" />
      </PromptInputSelectTrigger>
      <PromptInputSelectContent>
        {AGENT_MODE_OPTIONS.map(({ value: mode, label }) => {
          const Icon = MODE_ICONS[mode];

          return (
            <PromptInputSelectItem key={mode} value={mode}>
              <Icon className="h-4 w-4" />
              {label}
            </PromptInputSelectItem>
          );
        })}
      </PromptInputSelectContent>
    </PromptInputSelect>
  );
}
