import { useCallback } from "react";
import { buildLlmConfig, buildToolsPayload } from "@/lib/api";
import { type AgentMode, type ReasoningEffort } from "@/lib/types";
import { useSettingsStore } from "@/stores/settings-store";

export interface BuildRequestBodyArgs {
  agentMode: AgentMode;
  reasoningEffort: ReasoningEffort;
  includedDocuments: string[];
  excludedDocuments: string[];
}

export type BuildRequestBody = (modeOverride?: AgentMode) => Record<string, unknown>;

export function useBuildRequestBody({
  agentMode,
  reasoningEffort,
  includedDocuments,
  excludedDocuments,
}: BuildRequestBodyArgs): BuildRequestBody {
  const { overrides, personality, customSystemMessage, toolsSpec } = useSettingsStore();

  return useCallback(
    (modeOverride?: AgentMode) => ({
      personality,
      system_message: personality === "custom" ? customSystemMessage : undefined,
      reasoning_effort: reasoningEffort,
      mode: modeOverride ?? agentMode,
      llm: buildLlmConfig(overrides),
      included_documents: includedDocuments,
      excluded_documents: excludedDocuments,
      tools: buildToolsPayload(toolsSpec),
    }),
    [
      personality,
      customSystemMessage,
      reasoningEffort,
      agentMode,
      overrides,
      includedDocuments,
      excludedDocuments,
      toolsSpec,
    ],
  );
}
