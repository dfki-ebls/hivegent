import type { ReactNode } from "react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
 * differs from `initialValue`, so a rename that keeps the current name (empty
 * initial value) is a no-op either way.
 */
export function NameInputDialog({
  open,
  onOpenChange,
  title,
  description,
  label,
  initialValue = "",
  placeholder,
  submitLabel,
  onSubmit,
}: NameInputDialogProps) {
  const [name, setName] = useState(initialValue);

  const handleOpen = (isOpen: boolean) => {
    if (isOpen) {
      setName(initialValue);
    }

    onOpenChange(isOpen);
  };

  const trimmed = name.trim();
  const canSubmit = trimmed.length > 0 && trimmed !== initialValue;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (canSubmit) {
      onSubmit(trimmed);
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="name-input" className="text-sm font-medium">
              {label}
            </label>
            <Input
              id="name-input"
              value={name}
              placeholder={placeholder}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
