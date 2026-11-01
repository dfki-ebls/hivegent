import { type ComponentProps, type ReactNode, useState } from "react";
import {
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from "@/components/ui/trace";

export type TraceStepStatus = ComponentProps<typeof TraceLine>["status"];

interface TraceStepProps {
  icon: ReactNode;
  title: ReactNode;
  status?: TraceStepStatus;
  description?: ReactNode;
}

/** One step of a trace: its icon, its title, and an optional description. */
export function TraceStep({ icon, title, status, description }: TraceStepProps) {
  return (
    <TraceLine status={status}>
      <TraceIcon>{icon}</TraceIcon>
      <TraceTitle>{title}</TraceTitle>
      {description && <TraceDescription>{description}</TraceDescription>}
    </TraceLine>
  );
}

interface TraceStepCollapsibleProps extends TraceStepProps {
  /** Whether the step is open until the user toggles it, such as while it runs. */
  autoOpen?: boolean;
  /** Whether the detail stays mounted while closed, so the steps nested in it keep their state. */
  keepMounted?: boolean;
  contentClassName?: string;
  /** The detail the step opens to, a plain step without it. */
  children?: ReactNode;
}

/** A step that opens to its detail, where the user's toggle wins over `autoOpen`. */
export function TraceStepCollapsible({
  autoOpen = false,
  keepMounted,
  contentClassName,
  children,
  ...step
}: TraceStepCollapsibleProps) {
  const [open, setOpen] = useState<boolean>();

  if (!children) return <TraceStep {...step} />;

  return (
    <TraceCollapsible open={open ?? autoOpen} onOpenChange={setOpen}>
      <TraceCollapsibleTrigger>
        <TraceStep {...step} />
      </TraceCollapsibleTrigger>
      <TraceCollapsibleContent keepMounted={keepMounted} className={contentClassName}>
        {children}
      </TraceCollapsibleContent>
    </TraceCollapsible>
  );
}
