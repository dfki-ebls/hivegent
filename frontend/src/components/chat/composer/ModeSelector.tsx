import {
  CircleQuestionMarkIcon,
  EyeIcon,
  ListChecksIcon,
  PencilIcon,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ComposerSelect } from "@/components/chat/composer/ComposerSelect";
import { AGENT_MODES, type AgentMode } from "@/lib/types";

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
  const { t } = useTranslation();

  return (
    <ComposerSelect
      value={value}
      onChange={onChange}
      icon={MODE_ICONS[value]}
      options={AGENT_MODES.map((mode) => ({
        value: mode,
        label: t(($) => $.options.mode[mode]),
        icon: MODE_ICONS[mode],
      }))}
    />
  );
}
