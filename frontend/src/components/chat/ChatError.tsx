import { AlertCircle, RefreshCcwIcon, X } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

interface ChatErrorProps {
  message: string;
  onRetry: () => void;
  onDismiss: () => void;
}

export function ChatError({ message, onRetry, onDismiss }: ChatErrorProps) {
  return (
    <Alert variant="destructive">
      <AlertCircle className="h-4 w-4" />
      <AlertTitle>Error</AlertTitle>
      <AlertDescription className="flex items-start justify-between gap-2">
        <span>{message}</span>
        <span className="flex shrink-0 gap-1">
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCcwIcon className="mr-1 h-3 w-3" />
            Retry
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
