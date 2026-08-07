import {
  CircleQuestionMarkIcon,
  EyeIcon,
  ListChecksIcon,
  PencilIcon,
  type LucideIcon,
} from "lucide-react";
import { ComposerSelect } from "@/components/chat/composer/ComposerSelect";
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

const MODE_OPTIONS = AGENT_MODE_OPTIONS.map((option) => ({
  ...option,
  icon: MODE_ICONS[option.value],
}));

export function ModeSelector({ value, onChange }: ModeSelectorProps) {
  return (
    <ComposerSelect
      value={value}
      onChange={onChange}
      icon={MODE_ICONS[value]}
      options={MODE_OPTIONS}
    />
  );
}
