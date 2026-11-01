import type { ComponentProps, ReactElement } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Gives the control *children* a tooltip, which also names it for screen readers. */
export function WithTooltip({ label, children }: { label: string; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger render={children} aria-label={label} />
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

interface TooltipButtonProps extends ComponentProps<typeof Button> {
  label: string;
  /** Tints the icon of an action that deletes something, also while hovered. */
  destructive?: boolean;
}

/** An icon button named by *label*, which its tooltip shows and screen readers read. */
export function TooltipButton({
  label,
  destructive = false,
  variant = "ghost",
  size = "icon-sm",
  className,
  ...props
}: TooltipButtonProps) {
  return (
    <WithTooltip label={label}>
      <Button
        variant={variant}
        size={size}
        className={cn(destructive && "text-destructive hover:text-destructive", className)}
        {...props}
      />
    </WithTooltip>
  );
}
