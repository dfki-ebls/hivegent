import { PlusIcon, Trash2Icon } from "lucide-react";
import { type RefObject, type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type Option, OptionSelect } from "@/components/option-select";
import { TooltipButton } from "@/components/common/tooltip-button";
import { Button } from "@/components/ui/button";
import { FieldError } from "@/components/ui/field";
import { keyPrefix } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  ANSWERABILITIES,
  KEY_FACT_IMPORTANCES,
  PASSAGE_GRADES,
  QUESTION_TYPES,
} from "@/lib/ragold";

export const T_OPTIONS = keyPrefix(($) => $.chat.ragold);

export function useOptions() {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return {
    questionTypes: QUESTION_TYPES.map((value) => ({
      value,
      label: t(($) => $.questionTypes[value]),
    })),
    answerabilities: ANSWERABILITIES.map((value) => ({
      value,
      label: t(($) => $.answerabilities[value]),
    })),
    grades: PASSAGE_GRADES.map((value) => ({ value, label: t(($) => $.grades[value]) })),
    importances: KEY_FACT_IMPORTANCES.map((value) => ({
      value,
      label: t(($) => $.importances[value]),
    })),
  };
}

/** The trimmed text selected in *ref*, and a way to collapse the selection once it is used. */
export function useTextSelection(ref: RefObject<HTMLTextAreaElement | null>) {
  const [text, setText] = useState("");

  const onSelect = ({
    currentTarget: { value, selectionStart, selectionEnd },
  }: SyntheticEvent<HTMLTextAreaElement>) =>
    setText(value.slice(selectionStart, selectionEnd).trim());

  const clear = () => {
    setText("");
    ref.current?.setSelectionRange(ref.current.selectionEnd, ref.current.selectionEnd);
  };

  return { text, onSelect, clear };
}

/** One button per option that adds the selected text with that option, so each pick is explicit. */
export function AddButtons<T extends string>({
  options,
  disabled,
  onAdd,
}: {
  options: readonly Option<T>[];
  disabled: boolean;
  onAdd: (value: T) => void;
}) {
  return options.map((option) => (
    <Button
      key={option.value}
      variant="outline"
      disabled={disabled}
      onClick={() => onAdd(option.value)}
    >
      <PlusIcon data-icon="inline-start" />
      {option.label}
    </Button>
  ));
}

/** An excerpt with its choice, such as a passage with its grade, and an action to remove it. */
export function ExcerptRow<T extends string>({
  text,
  value,
  options,
  label,
  removeLabel,
  error,
  className,
  onChange,
  onRemove,
}: {
  className?: string;
  text: string;
  value: T | null;
  options: readonly Option<T>[];
  label: string;
  removeLabel: string;
  error?: string;
  onChange: (value: T) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <div className={cn("flex items-start gap-2", className)}>
      <div className="min-w-0 flex-1">
        <p className="line-clamp-4 font-mono text-xs whitespace-pre-wrap">{text}</p>
        {error && <FieldError>{error}</FieldError>}
      </div>
      <OptionSelect
        size="sm"
        aria-label={label}
        value={value}
        options={options}
        placeholder={t(($) => $.choose)}
        onChange={onChange}
      />
      <TooltipButton label={removeLabel} destructive onClick={onRemove}>
        <Trash2Icon />
      </TooltipButton>
    </div>
  );
}
