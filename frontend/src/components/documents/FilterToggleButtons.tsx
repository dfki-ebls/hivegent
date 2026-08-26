import { Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type FilterEntryState = "included" | "excluded" | undefined;

interface FilterToggleButtonsProps {
  /** Whether the entry is currently part of the chat document selection. */
  state: FilterEntryState;
  onInclude: () => void;
  onExclude: () => void;
  /** Compact 6x6 buttons with 3x3 icons for tree rows. */
  compact?: boolean;
  /** Hide inactive buttons until a `group` ancestor is hovered. */
  revealOnHover?: boolean;
}

/**
 * The Eye/EyeOff pair toggling an entry in the chat document selection. The
 * active direction stays visible and tinted so the state is readable directly
 * in document listings. Eye points the chat at an entry, EyeOff hides it: only
 * the latter restricts what the agent's tools return.
 */
export function FilterToggleButtons({
  state,
  onInclude,
  onExclude,
  compact = false,
  revealOnHover = false,
}: FilterToggleButtonsProps) {
  const iconSize = compact ? "h-3 w-3" : "h-4 w-4";
  // Collapse (not just fade) inactive buttons until the row is hovered, so an
  // idle row gives its full width to the name instead of reserving button space.
  const hidden = revealOnHover ? "hidden group-hover:inline-flex" : undefined;
  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className={cn(compact && "h-6 w-6", state === "included" ? "text-primary" : hidden)}
        title={state === "included" ? "Stop pointing chat at this" : "Point chat at this"}
        onClick={(e) => {
          e.stopPropagation();
          onInclude();
        }}
      >
        <Eye className={iconSize} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={cn(compact && "h-6 w-6", state === "excluded" ? "text-destructive" : hidden)}
        title={state === "excluded" ? "Stop excluding from chat" : "Exclude from chat"}
        onClick={(e) => {
          e.stopPropagation();
          onExclude();
        }}
      >
        <EyeOff className={iconSize} />
      </Button>
    </>
  );
}
