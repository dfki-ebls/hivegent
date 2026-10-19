import { ChevronRight, Scissors } from "lucide-react";
import { type ComponentProps, useId } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import { TooltipButton } from "@/components/TooltipButton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Item } from "@/components/ui/item";

/**
 * Fades a row action in while its `group/row` row is hovered or holds focus,
 * and keeps the hidden action from catching clicks.
 * A touch screen cannot hover, so there the action always shows.
 */
export const REVEAL_ON_HOVER =
  "pointer-events-none opacity-0 transition-opacity group-focus-within/row:pointer-events-auto group-focus-within/row:opacity-100 group-hover/row:pointer-events-auto group-hover/row:opacity-100 pointer-coarse:pointer-events-auto pointer-coarse:opacity-100";

/** The chevron that expands or collapses a row, also usable as a `CollapsibleTrigger` render. */
export function ExpandButton({
  expanded,
  ...props
}: Omit<ComponentProps<typeof TooltipButton>, "label"> & { expanded: boolean }) {
  const { t } = useTranslation();

  return (
    <TooltipButton
      size="icon-xs"
      label={expanded ? t(($) => $.documents.tree.collapse) : t(($) => $.documents.tree.expand)}
      {...props}
    >
      <ChevronRight className={cn("transition-transform", expanded && "rotate-90")} />
    </TooltipButton>
  );
}

/** A row's name as a truncating text button that opens or arms the entry, or a `render` link. */
export function NameButton({ className, children, ...props }: ComponentProps<typeof Button>) {
  return (
    <Button
      variant="link"
      className={cn("h-auto min-w-0 shrink justify-start p-0 text-foreground", className)}
      {...props}
    >
      <span className="truncate">{children}</span>
    </Button>
  );
}

/**
 * An `Item` that is one button as a whole, such as a selectable card or sidebar entry.
 * A button may only hold phrasing content, so it is stretched over the item and named by
 * the item's content rather than wrapping it.
 */
export function ItemButton({
  variant,
  size,
  className,
  children,
  ...props
}: ComponentProps<"button"> & Pick<ComponentProps<typeof Item>, "variant" | "size">) {
  const labelId = useId();

  return (
    <Item
      variant={variant}
      size={size}
      className={cn(
        "relative hover:bg-muted has-focus-visible:border-ring has-focus-visible:ring-[3px] has-focus-visible:ring-ring/50",
        className,
      )}
    >
      <button
        type="button"
        aria-labelledby={labelId}
        className="absolute inset-0 z-10 rounded-[inherit] outline-none"
        {...props}
      />
      <div id={labelId} className="contents">
        {children}
      </div>
    </Item>
  );
}

/** How many chunks a document row's file was split into. */
export function ChunkCountBadge({ count }: { count: number }) {
  return (
    <Badge variant="outline">
      <Scissors />
      {count}
    </Badge>
  );
}
