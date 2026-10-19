import type { FileUIPart } from "ai";
import { create } from "zustand";
import { type AgentMode, DEFAULT_REASONING_EFFORT, type ReasoningEffort } from "@/lib/types";

export interface ComposerFile {
  id: string;
  part: FileUIPart;
}

/**
 * The composer's draft and settings, kept outside the component so they survive
 * the remount when a draft becomes the conversation it minted, which happens
 * once its first reply finishes, often while the user types the next message.
 */
interface ComposerState {
  text: string;
  files: ComposerFile[];
  /** Whether the field had focus when the composer unmounted, so its successor takes it back. */
  focused: boolean;
  agentMode: AgentMode;
  reasoningEffort: ReasoningEffort;
  setText: (text: string) => void;
  setFiles: (update: (files: ComposerFile[]) => ComposerFile[]) => void;
  setFocused: (focused: boolean) => void;
  setAgentMode: (agentMode: AgentMode) => void;
  setReasoningEffort: (reasoningEffort: ReasoningEffort) => void;
  /** Empty the draft once it is sent, keeping the settings. */
  clearDraft: () => void;
}

export const useComposerStore = create<ComposerState>((set) => ({
  text: "",
  files: [],
  focused: false,
  agentMode: "interactive",
  reasoningEffort: DEFAULT_REASONING_EFFORT,
  setText: (text) => set({ text }),
  setFiles: (update) => set((state) => ({ files: update(state.files) })),
  setFocused: (focused) => set({ focused }),
  setAgentMode: (agentMode) => set({ agentMode }),
  setReasoningEffort: (reasoningEffort) => set({ reasoningEffort }),
  clearDraft: () => set({ text: "", files: [] }),
}));
