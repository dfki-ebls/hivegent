import { Eye, EyeOff } from "lucide-react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { cn } from "@/lib/utils";
import { TooltipButton } from "@/components/TooltipButton";
import { REVEAL_ON_HOVER } from "@/components/RowButtons";

const T_OPTIONS = keyPrefix(($) => $.documents.filter);

export type FilterEntryState = "included" | "excluded" | undefined;

interface FilterToggleButtonsProps {
  /** Whether the entry is currently part of the chat document selection. */
  state: FilterEntryState;
  onInclude: () => void;
  onExclude: () => void;
  /** Fade inactive buttons in only while their `group/row` row is hovered or holds focus. */
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
  revealOnHover = false,
}: FilterToggleButtonsProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const hidden = revealOnHover && REVEAL_ON_HOVER;
  const included = state === "included";
  const excluded = state === "excluded";

  return (
    <>
      <TooltipButton
        size="icon-xs"
        label={included ? t(($) => $.stopIncluding) : t(($) => $.include)}
        aria-pressed={included}
        className={cn(included ? "text-primary" : hidden)}
        onClick={onInclude}
      >
        <Eye />
      </TooltipButton>
      <TooltipButton
        size="icon-xs"
        label={excluded ? t(($) => $.stopExcluding) : t(($) => $.exclude)}
        aria-pressed={excluded}
        className={cn(excluded ? "text-destructive" : hidden)}
        onClick={onExclude}
      >
        <EyeOff />
      </TooltipButton>
    </>
  );
}
