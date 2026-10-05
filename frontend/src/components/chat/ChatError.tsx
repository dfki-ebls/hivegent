import { AlertCircle, RefreshCcwIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ChatAlert } from "@/components/chat/ChatAlert";

interface ChatErrorProps {
  message: string;
  onRetry: () => void;
  onDismiss: () => void;
}

export function ChatError({ message, onRetry, onDismiss }: ChatErrorProps) {
  const { t } = useTranslation();

  return (
    <ChatAlert
      icon={AlertCircle}
      title={t(($) => $.common.states.error)}
      message={message}
      actionIcon={RefreshCcwIcon}
      actionLabel={t(($) => $.common.actions.retry)}
      onAction={onRetry}
      onDismiss={onDismiss}
    />
  );
}
