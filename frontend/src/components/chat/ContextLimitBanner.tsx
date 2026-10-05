import { Minimize2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ChatAlert } from "@/components/chat/ChatAlert";
import { keyPrefix } from "@/i18n";

const T_OPTIONS = keyPrefix(($) => $.chat.contextLimit);

interface ContextLimitBannerProps {
  disabled: boolean;
  onCompact: () => void;
  onDismiss: () => void;
}

export function ContextLimitBanner({ disabled, onCompact, onDismiss }: ContextLimitBannerProps) {
  const { t } = useTranslation(undefined, T_OPTIONS);

  return (
    <ChatAlert
      icon={Minimize2}
      title={t(($) => $.title)}
      message={t(($) => $.message)}
      actionIcon={Minimize2}
      actionLabel={t(($) => $.action)}
      actionDisabled={disabled}
      onAction={onCompact}
      onDismiss={onDismiss}
    />
  );
}
