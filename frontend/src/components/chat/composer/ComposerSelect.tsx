import { ChevronDownIcon, type LucideIcon } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InputGroupButton } from "@/components/ui/input-group";

interface ComposerSelectProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  icon: LucideIcon;
  options: readonly { value: T; label: string; icon?: LucideIcon }[];
  className?: string;
}

/** A menu in the composer's toolbar that picks one of *options*. */
export function ComposerSelect<T extends string>({
  value,
  onChange,
  icon: Icon,
  options,
  className,
}: ComposerSelectProps<T>) {
  const current = options.find((option) => option.value === value);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<InputGroupButton size="sm" variant="outline" className={className} />}
      >
        <Icon data-icon="inline-start" />
        {current?.label}
        <ChevronDownIcon data-icon="inline-end" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(next) => {
            const option = options.find((candidate) => candidate.value === next);
            if (option) onChange(option.value);
          }}
        >
          {options.map(({ value: option, label, icon: OptionIcon }) => (
            <DropdownMenuRadioItem key={option} value={option} closeOnClick>
              {OptionIcon && <OptionIcon />}
              {label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
