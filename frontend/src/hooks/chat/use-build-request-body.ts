import { useCallback } from "react";
import { buildLlmConfig, buildModePayload, buildToolsPayload } from "@/lib/api";
import { type AgentMode, type ReasoningEffort } from "@/lib/types";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { useSettingsStore } from "@/stores/settings-store";

export interface BuildRequestBodyArgs {
  agentMode: AgentMode;
  reasoningEffort: ReasoningEffort;
}

export type BuildRequestBody = (modeOverride?: AgentMode) => Record<string, unknown>;

export function useBuildRequestBody({
  agentMode,
  reasoningEffort,
}: BuildRequestBodyArgs): BuildRequestBody {
  const { overrides, personality, customSystemMessage, toolsSpec } = useSettingsStore();
  const included = useDocumentFilterStore((s) => s.included);
  const excluded = useDocumentFilterStore((s) => s.excluded);

  return useCallback(
    (modeOverride?: AgentMode) => ({
      personality,
      system_message: personality === "custom" ? customSystemMessage : undefined,
      reasoning_effort: reasoningEffort,
      mode: buildModePayload(modeOverride ?? agentMode),
      llm: buildLlmConfig(overrides),
      included_documents: included,
      excluded_documents: excluded,
      tools: buildToolsPayload(toolsSpec),
    }),
    [
      personality,
      customSystemMessage,
      reasoningEffort,
      agentMode,
      overrides,
      included,
      excluded,
      toolsSpec,
    ],
  );
}
