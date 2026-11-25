import { BotIcon } from "lucide-react";
import { useSetPreferences } from "@/api/preferences";
import { useServerSettings } from "@/api/settings";
import { ComposerSelect } from "@/components/chat/composer/composer-select";
import { useChatModel } from "@/hooks/use-chat-model";

/** Pick the chat model among the catalog's, shown only when there is a choice. */
export function ModelSelector() {
  const models = useServerSettings((settings) => settings.models) ?? [];
  const model = useChatModel();
  const setPreferences = useSetPreferences();

  if (models.length < 2 || model === undefined) {
    return null;
  }

  return (
    <ComposerSelect
      value={model.id}
      onChange={(id) => setPreferences({ model: id })}
      icon={BotIcon}
      options={models.map(({ id, label }) => ({ value: id, label }))}
    />
  );
}
