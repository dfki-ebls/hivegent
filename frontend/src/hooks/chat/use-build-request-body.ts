import { useCallback } from "react";
import { buildLlmConfig, buildToolsPayload } from "@/lib/api";
import type { ChatRequestConfig } from "@/lib/types";
import { useComposerStore } from "@/stores/composer-store";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { useSettingsStore } from "@/stores/settings-store";

export type BuildRequestBody = () => ChatRequestConfig;

export function useBuildRequestBody(): BuildRequestBody {
  const { overrides, personality, customSystemMessage, toolsSpec } = useSettingsStore();
  const included = useDocumentFilterStore((s) => s.included);
  const excluded = useDocumentFilterStore((s) => s.excluded);

  // Mode and effort are read when a request goes out, so changing them re-renders only the composer.
  return useCallback(() => {
    const { agentMode, reasoningEffort } = useComposerStore.getState();

    return {
      personality,
      system_message: personality === "custom" ? customSystemMessage : undefined,
      reasoning_effort: reasoningEffort,
      mode: agentMode,
      llm: buildLlmConfig(overrides),
      included_documents: included,
      excluded_documents: excluded,
      tools: buildToolsPayload(toolsSpec),
    };
  }, [personality, customSystemMessage, overrides, included, excluded, toolsSpec]);
}
