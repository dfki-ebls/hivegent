import { Minimize2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "@/components/ErrorAlert";
import { Button } from "@/components/ui/button";
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
    <ErrorAlert
      icon={<Minimize2Icon />}
      title={t(($) => $.title)}
      onDismiss={onDismiss}
      action={
        <Button variant="outline" size="sm" onClick={onCompact} disabled={disabled}>
          <Minimize2Icon data-icon="inline-start" />
          {t(($) => $.action)}
        </Button>
      }
    >
      {t(($) => $.message)}
    </ErrorAlert>
  );
}
