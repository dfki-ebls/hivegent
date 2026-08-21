import { AlertCircle, RefreshCcwIcon } from "lucide-react";
import { ChatAlert } from "@/components/chat/ChatAlert";

interface ChatErrorProps {
  message: string;
  onRetry: () => void;
  onDismiss: () => void;
}

export function ChatError({ message, onRetry, onDismiss }: ChatErrorProps) {
  return (
    <ChatAlert
      icon={AlertCircle}
      title="Error"
      message={message}
      actionIcon={RefreshCcwIcon}
      actionLabel="Retry"
      onAction={onRetry}
      onDismiss={onDismiss}
    />
  );
}
