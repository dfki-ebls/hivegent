import type { LucideIcon } from "lucide-react";
import type { ComponentProps } from "react";

/**
 * Link opening in a new tab behind a leading icon.
 * Meant as the `render` element of a `DropdownMenuItem` or `Button`, which lay
 * out and size its icon, so every anchor prop is forwarded.
 */
export function NewTabLink({
  icon: Icon,
  children,
  ...props
}: ComponentProps<"a"> & { icon: LucideIcon }) {
  return (
    <a target="_blank" rel="noreferrer" {...props}>
      <Icon data-icon="inline-start" />
      {children}
    </a>
  );
}
