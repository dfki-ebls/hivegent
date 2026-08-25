import type { LucideIcon } from "lucide-react";
import {
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSelectValue,
} from "@/components/ai-elements/prompt-input";

interface ComposerSelectProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  icon: LucideIcon;
  options: readonly { value: T; label: string; icon?: LucideIcon }[];
}

export function ComposerSelect<T extends string>({
  value,
  onChange,
  icon: Icon,
  options,
}: ComposerSelectProps<T>) {
  return (
    <PromptInputSelect value={value} onValueChange={(v) => onChange(v as T)}>
      <PromptInputSelectTrigger className="h-8 w-auto min-w-20">
        <Icon className="h-4 w-4" />
        <PromptInputSelectValue />
      </PromptInputSelectTrigger>
      <PromptInputSelectContent>
        {options.map(({ value: option, label, icon: OptionIcon }) => (
          <PromptInputSelectItem key={option} value={option}>
            {OptionIcon && <OptionIcon className="h-4 w-4" />}
            {label}
          </PromptInputSelectItem>
        ))}
      </PromptInputSelectContent>
    </PromptInputSelect>
  );
}
