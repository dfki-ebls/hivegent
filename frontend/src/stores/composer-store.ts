import { create } from "zustand";
import {
  type AgentMode,
  DEFAULT_REASONING_EFFORT,
  type ReasoningEffort,
} from "@/api/conversations";

/** The composer's settings, which carry over from one conversation to the next. */
interface ComposerState {
  agentMode: AgentMode;
  reasoningEffort: ReasoningEffort;
  setAgentMode: (agentMode: AgentMode) => void;
  setReasoningEffort: (reasoningEffort: ReasoningEffort) => void;
}

export const useComposerStore = create<ComposerState>((set) => ({
  agentMode: "interactive",
  reasoningEffort: DEFAULT_REASONING_EFFORT,
  setAgentMode: (agentMode) => set({ agentMode }),
  setReasoningEffort: (reasoningEffort) => set({ reasoningEffort }),
}));
