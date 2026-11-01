import { BotIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

// EU AI Act Article 50(1): users must be informed they are interacting with an
// AI system, clearly and distinguishably, at the latest at the first
// interaction (Article 50(5)). A persistent marker under the composer keeps the
// notice visible for the whole session, which the Commission guidance favours
// over a one-time disclosure.
export function AiDisclosure() {
  const { t } = useTranslation();

  return (
    <div
      role="note"
      className="flex items-start justify-center gap-1.5 text-center text-xs leading-snug text-muted-foreground"
    >
      <BotIcon className="mt-px size-3 shrink-0" aria-hidden />
      <span>{t(($) => $.chat.disclosure.notice)}</span>
    </div>
  );
}
