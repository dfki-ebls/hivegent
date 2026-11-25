import { BrainIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ComposerSelect } from "@/components/chat/composer/composer-select";
import { clampReasoningEffort, type ReasoningEffort } from "@/api/conversations";

interface ReasoningEffortSelectorProps {
  value: ReasoningEffort;
  /** The efforts the chosen model offers, which hide the selector when there are none. */
  levels: readonly ReasoningEffort[];
  className?: string;
  onChange: (value: ReasoningEffort) => void;
}

export function ReasoningEffortSelector({
  value,
  levels,
  onChange,
  className,
}: ReasoningEffortSelectorProps) {
  const { t } = useTranslation();
  const shown = clampReasoningEffort(value, levels);

  if (shown === undefined) {
    return null;
  }

  return (
    <ComposerSelect
      value={shown}
      onChange={onChange}
      className={className}
      icon={BrainIcon}
      options={levels.map((effort) => ({
        value: effort,
        label: t(($) => $.options.reasoningEffort[effort]),
      }))}
    />
  );
}
