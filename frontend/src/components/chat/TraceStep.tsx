import type { ComponentProps, ReactNode } from "react";
import {
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

/** A step that opens its `TraceCollapsible`. */
export function TraceStepTrigger(props: TraceStepProps) {
  return (
    <TraceCollapsibleTrigger>
      <TraceStep {...props} />
    </TraceCollapsibleTrigger>
  );
}
