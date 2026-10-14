import type { ComponentProps } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface Option<T extends string> {
  value: T;
  label: string;
  description?: string;
}

interface OptionSelectProps<T extends string> extends Pick<
  ComponentProps<typeof SelectTrigger>,
  "id" | "className" | "size"
> {
  value: T;
  options: readonly Option<T>[];
  onChange: (value: T) => void;
  placeholder?: string;
  disabled?: boolean;
}

/** A select of labelled options, whose trigger shows the label of the chosen one. */
export function OptionSelect<T extends string>({
  value,
  options,
  onChange,
  placeholder,
  disabled,
  ...trigger
}: OptionSelectProps<T>) {
  return (
    <Select
      value={value}
      items={options}
      disabled={disabled}
      onValueChange={(next) => next !== null && onChange(next)}
    >
      <SelectTrigger {...trigger}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value} title={option.description}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
