import type { LucideIcon } from "lucide-react";
import { type ComponentProps, type ReactNode, useId } from "react";
import { Field, FieldLabel } from "@/components/ui/field";
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

interface LabeledOptionSelectProps<T extends string> extends Omit<
  OptionSelectProps<T>,
  "id" | "size"
> {
  icon: LucideIcon;
  label: string;
  /** Controls after the select, such as a configuration button. */
  children?: ReactNode;
}

/** A small option select in a toolbar row, behind a muted label with an icon. */
export function LabeledOptionSelect<T extends string>({
  icon: Icon,
  label,
  children,
  ...select
}: LabeledOptionSelectProps<T>) {
  const id = useId();

  return (
    <Field orientation="horizontal" className="w-auto">
      <FieldLabel htmlFor={id} className="text-muted-foreground">
        <Icon className="size-4" />
        {label}
      </FieldLabel>
      <OptionSelect id={id} size="sm" {...select} />
      {children}
    </Field>
  );
}
