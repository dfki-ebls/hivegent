import type { LucideIcon } from "lucide-react";
import { X } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

interface ChatAlertProps {
  icon: LucideIcon;
  title: string;
  message: string;
  actionIcon: LucideIcon;
  actionLabel: string;
  actionDisabled?: boolean;
  onAction: () => void;
  onDismiss: () => void;
}

/**
 * The one destructive banner shape the message list renders, whatever the
 * failure was: an icon and title, the message, one recovery action, and a
 * dismiss. Every caller differs only in those words, so the layout is not
 * restated per failure kind.
 */
export function ChatAlert({
  icon: Icon,
  title,
  message,
  actionIcon: ActionIcon,
  actionLabel,
  actionDisabled,
  onAction,
  onDismiss,
}: ChatAlertProps) {
  return (
    <Alert variant="destructive">
      <Icon className="h-4 w-4" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex items-start justify-between gap-2">
        <span>{message}</span>
        <span className="flex shrink-0 gap-1">
          <Button variant="outline" size="sm" onClick={onAction} disabled={actionDisabled}>
            <ActionIcon className="mr-1 h-3 w-3" />
            {actionLabel}
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={onDismiss}>
            <X className="h-3 w-3" />
            <span className="sr-only">Dismiss</span>
          </Button>
        </span>
      </AlertDescription>
    </Alert>
  );
}
