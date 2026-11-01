import { CircleAlertIcon, XIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { TooltipButton } from "@/components/common/tooltip-button";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { errorMessage } from "@/lib/utils";

interface ErrorAlertProps {
  title?: ReactNode;
  /** The failure, whose message is shown unless *children* replace it. */
  error?: unknown;
  children?: ReactNode;
  /** A recovery action below the message. */
  action?: ReactNode;
  onDismiss?: () => void;
}

/** A failure with an optional recovery action and a dismiss button, nothing without one. */
export function ErrorAlert({ title, error, children, action, onDismiss }: ErrorAlertProps) {
  const { t } = useTranslation();

  if (!error && !children) return null;

  return (
    <Alert variant="destructive">
      <CircleAlertIcon />
      {title && <AlertTitle>{title}</AlertTitle>}
      <AlertDescription className="flex flex-col items-start gap-2">
        {children ?? errorMessage(error)}
        {action}
      </AlertDescription>
      {onDismiss && (
        <AlertAction>
          <TooltipButton
            label={t(($) => $.common.actions.dismiss)}
            size="icon-xs"
            onClick={onDismiss}
          >
            <XIcon />
          </TooltipButton>
        </AlertAction>
      )}
    </Alert>
  );
}
