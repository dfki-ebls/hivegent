import { Minimize2 } from "lucide-react";
import { ChatAlert } from "@/components/chat/ChatAlert";

interface ContextLimitBannerProps {
  disabled: boolean;
  onCompact: () => void;
  onDismiss: () => void;
}

export function ContextLimitBanner({ disabled, onCompact, onDismiss }: ContextLimitBannerProps) {
  return (
    <ChatAlert
      icon={Minimize2}
      title="Context limit reached"
      message="Compact this conversation to summarize its history, then retry the last message."
      actionIcon={Minimize2}
      actionLabel="Compact and retry"
      actionDisabled={disabled}
      onAction={onCompact}
      onDismiss={onDismiss}
    />
  );
}
