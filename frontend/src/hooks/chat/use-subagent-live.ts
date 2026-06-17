import { createContext, useContext } from "react";
import type { SubagentStep, SubagentSteps } from "@/lib/chat/subagent";

/**
 * Live subagent transcripts for the current turn, keyed by parent tool-call id.
 *
 * Populated from transient `data-subagent` parts in `useHivegentChat` and read
 * by the `explore` tool renderer; transient parts never reach `message.parts`,
 * so this context is the only path for live (pre-completion) subagent activity.
 */
const SubagentLiveContext = createContext<SubagentSteps>(new Map());

export const SubagentLiveProvider = SubagentLiveContext.Provider;

export function useSubagentLive(toolCallId: string | undefined): SubagentStep[] | undefined {
  const live = useContext(SubagentLiveContext);
  return toolCallId ? live.get(toolCallId) : undefined;
}
