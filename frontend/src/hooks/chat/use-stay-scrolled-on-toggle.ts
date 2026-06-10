import { useStickToBottomContext } from "use-stick-to-bottom";

/**
 * Collapsible `onOpenChange` handler that keeps the conversation scroll
 * position put when a tool or plan card is expanded.
 *
 * The enclosing `Conversation` (use-stick-to-bottom) reads the card's growth as
 * a positive resize and, while the view is bottom-locked, snaps to the very
 * bottom — yanking the card the user just opened out of sight. Releasing the
 * lock with `stopScroll` before that resize lands makes it a no-op, so the view
 * stays where the user clicked. Collapsing shrinks the content and never snaps,
 * so only expansion needs handling.
 */
export function useStayScrolledOnToggle(): (open: boolean) => void {
  const { stopScroll } = useStickToBottomContext();
  return (open) => {
    if (open) stopScroll();
  };
}
