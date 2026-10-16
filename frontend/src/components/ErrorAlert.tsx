import { CircleAlertIcon, XIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { TooltipButton } from "@/components/TooltipButton";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface ErrorAlertProps {
  title?: ReactNode;
  icon?: ReactNode;
  /** The error message. */
  children: ReactNode;
  /** A recovery action below the message. */
  action?: ReactNode;
  onDismiss?: () => void;
}

/** A failure, with an optional recovery action and a dismiss button. */
export function ErrorAlert({
  title,
  icon = <CircleAlertIcon />,
  children,
  action,
  onDismiss,
}: ErrorAlertProps) {
  const { t } = useTranslation();

  return (
    <Alert variant="destructive">
      {icon}
      {title && <AlertTitle>{title}</AlertTitle>}
      <AlertDescription className="flex flex-col items-start gap-2">
        {children}
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
