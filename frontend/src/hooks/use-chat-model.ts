import { usePreferences } from "@/api/preferences";
import { type BackendSettings, type ModelInfo, useServerSettings } from "@/api/settings";

/** The catalog model *chosen* names, the default one when it names none the catalog lists. */
function chatModel(settings: BackendSettings, chosen: string | null): ModelInfo | undefined {
  const byId = (id: string | null) => settings.models.find((model) => model.id === id);

  return byId(chosen) ?? byId(settings.default_model);
}

/** The chat model the user's runs use, undefined until the settings load or without a catalog. */
export function useChatModel(): ModelInfo | undefined {
  const chosen = usePreferences().model;

  return useServerSettings((settings) => chatModel(settings, chosen));
}
