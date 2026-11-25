import { useQuery } from "@tanstack/react-query";
import { ContactRoundIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { assistantQueries } from "@/api/assistants";
import { usePreferences, useSetPreferences } from "@/api/preferences";
import { ComposerSelect } from "@/components/chat/composer/composer-select";

/**
 * Pick the assistant the chat runs as, shown only when the user sees any.
 * The server reports a choice gone from the library as none.
 */
export function AssistantSelector() {
  const { t } = useTranslation();
  const { data: assistants = [] } = useQuery(assistantQueries.list());
  const chosen = usePreferences().assistant;
  const setPreferences = useSetPreferences();
  if (assistants.length === 0) return null;

  return (
    <ComposerSelect
      value={chosen ?? ""}
      onChange={(value) => setPreferences({ assistant: value || null })}
      icon={ContactRoundIcon}
      options={[
        { value: "", label: t(($) => $.chat.composer.noAssistant) },
        ...assistants.map(({ ref }) => ({ value: ref, label: ref })),
      ]}
    />
  );
}
