import type { ChatStatus } from "ai";
import { ArrowUpIcon, SquareIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { InputGroupButton } from "@/components/ui/input-group";
import { Spinner } from "@/components/ui/spinner";
import { keyPrefix } from "@/i18n";
import { cn } from "@/lib/utils";

const T_OPTIONS = keyPrefix(($) => $.chat.composer);

interface SubmitStopButtonProps {
  status: ChatStatus;
  /** Whether a turn runs, which the button then stops instead of submitting. */
  streaming: boolean;
  /** Whether there is nothing to submit, which never keeps a running turn from stopping. */
  empty?: boolean;
  onStop: () => void;
  className?: string;
}

/** The round button of a chat form, which submits it or stops the running turn. */
export function SubmitStopButton({
  status,
  streaming,
  empty = false,
  onStop,
  className,
}: SubmitStopButtonProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <InputGroupButton
      type={streaming ? "button" : "submit"}
      size="icon-sm"
      variant="default"
      className={cn("rounded-full", className)}
      aria-label={streaming ? t(($) => $.stop) : t(($) => $.submit)}
      disabled={!streaming && empty}
      onClick={streaming ? onStop : undefined}
    >
      {status === "submitted" ? <Spinner /> : streaming ? <SquareIcon /> : <ArrowUpIcon />}
    </InputGroupButton>
  );
}
