import { AlertCircle, X } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

interface ErrorBannerProps {
  message: string;
  onDismiss: () => void;
}

export function ErrorBanner({ message, onDismiss }: ErrorBannerProps) {
  return (
    <Alert variant="destructive" className="m-4 mb-0">
      <AlertCircle className="h-4 w-4" />
      <AlertDescription className="flex items-center justify-between">
        <span>{message}</span>
        <Button variant="ghost" size="sm" className="h-auto p-1" onClick={onDismiss}>
          <X className="h-4 w-4" />
        </Button>
      </AlertDescription>
    </Alert>
  );
}
