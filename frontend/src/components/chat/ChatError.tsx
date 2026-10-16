import { RefreshCcwIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "@/components/ErrorAlert";
import { Button } from "@/components/ui/button";

interface ChatErrorProps {
  message: string;
  onRetry: () => void;
  onDismiss: () => void;
}

export function ChatError({ message, onRetry, onDismiss }: ChatErrorProps) {
  const { t } = useTranslation();

  return (
    <ErrorAlert
      title={t(($) => $.common.states.error)}
      onDismiss={onDismiss}
      action={
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCcwIcon data-icon="inline-start" />
          {t(($) => $.common.actions.retry)}
        </Button>
      }
    >
      {message}
    </ErrorAlert>
  );
}
