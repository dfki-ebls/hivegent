import { BrainIcon } from "lucide-react";
import { ComposerSelect } from "@/components/chat/composer/ComposerSelect";
import { REASONING_EFFORT_OPTIONS, type ReasoningEffort } from "@/lib/types";

interface ReasoningEffortSelectorProps {
  value: ReasoningEffort;
  onChange: (value: ReasoningEffort) => void;
}

export function ReasoningEffortSelector({ value, onChange }: ReasoningEffortSelectorProps) {
  return (
    <ComposerSelect
      value={value}
      onChange={onChange}
      icon={BrainIcon}
      options={REASONING_EFFORT_OPTIONS}
    />
  );
}
