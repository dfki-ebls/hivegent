import type { ChatStatus } from "ai";
import { ArrowUpIcon, SquareIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { InputGroupButton } from "@/components/ui/input-group";
import { Spinner } from "@/components/common/spinner";
import { keyPrefix } from "@/i18n";
import { isChatBusy } from "@/lib/chat/chat-utils";
import { cn } from "@/lib/utils";

const T_OPTIONS = keyPrefix(($) => $.chat.composer);

interface SubmitStopButtonProps {
  /** The chat's status, a running turn the button stops instead of submitting. */
  status: ChatStatus;
  /** Whether there is nothing to submit, which never keeps a running turn from stopping. */
  empty?: boolean;
  onStop: () => void;
  className?: string;
}

/** The round button of a chat form, which submits it or stops the running turn. */
export function SubmitStopButton({
  status,
  empty = false,
  onStop,
  className,
}: SubmitStopButtonProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const streaming = isChatBusy(status);

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
