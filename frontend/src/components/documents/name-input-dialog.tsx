import { type FormEvent, type ReactNode, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

interface NameInputDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  label: string;
  /** Value prefilled into the input each time the dialog opens. */
  initialValue?: string;
  placeholder?: string;
  submitLabel: string;
  /** Called with the trimmed value; only invocable when it is non-empty and changed. */
  onSubmit: (value: string) => void;
}

/**
 * A modal that prompts for a single name, shared by the create-folder and
 * rename flows. Submit is disabled until the trimmed value is non-empty and
 * differs from `initialValue`, so a rename that keeps the current name is a
 * no-op either way.
 */
export function NameInputDialog({ open, onOpenChange, ...form }: NameInputDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <NameInputForm {...form} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

// Mounts with the dialog content, so every opening starts from `initialValue`.
function NameInputForm({
  title,
  description,
  label,
  initialValue = "",
  placeholder,
  submitLabel,
  onSubmit,
  onDone,
}: Omit<NameInputDialogProps, "open" | "onOpenChange"> & { onDone: () => void }) {
  const { t } = useTranslation();
  const id = useId();
  const [name, setName] = useState(initialValue);
  const trimmed = name.trim();
  const canSubmit = trimmed.length > 0 && trimmed !== initialValue;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();

    if (canSubmit) {
      onSubmit(trimmed);
      onDone();
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      <Field>
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        <Input
          id={id}
          value={name}
          placeholder={placeholder}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" />}>
          {t(($) => $.common.actions.cancel)}
        </DialogClose>
        <Button type="submit" disabled={!canSubmit}>
          {submitLabel}
        </Button>
      </DialogFooter>
    </form>
  );
}
