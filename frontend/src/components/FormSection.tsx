import type { ReactNode } from "react";
import { Label } from "@/components/ui/label";

interface FormSectionProps {
  label: string;
  htmlFor?: string;
  description?: ReactNode;
  children: ReactNode;
}

/** A labeled dialog field with an optional hint below it. */
export function FormSection({ label, htmlFor, description, children }: FormSectionProps) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  );
}
