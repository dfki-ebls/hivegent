import { BrainIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ComposerSelect } from "@/components/chat/composer/ComposerSelect";
import { REASONING_EFFORTS, type ReasoningEffort } from "@/lib/types";

interface ReasoningEffortSelectorProps {
  value: ReasoningEffort;
  className?: string;
  onChange: (value: ReasoningEffort) => void;
}

export function ReasoningEffortSelector({ value, onChange, className }: ReasoningEffortSelectorProps) {
  const { t } = useTranslation();

  return (
    <ComposerSelect
      value={value}
      onChange={onChange}
      className={className}
      icon={BrainIcon}
      options={REASONING_EFFORTS.map((effort) => ({
        value: effort,
        label: t(($) => $.options.reasoningEffort[effort]),
      }))}
    />
  );
}
