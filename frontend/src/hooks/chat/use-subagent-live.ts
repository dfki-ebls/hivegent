import { useCallback, useSyncExternalStore } from "react";
import { useChatContext } from "@/components/chat/chat-context";
import { type SubagentStep, SubagentLive } from "@/lib/chat/subagent";

const NO_SUBAGENTS = new SubagentLive();

/** The live transcript of the subagent the call *toolCallId* runs, re-rendering only on its updates. */
export function useSubagentLive(toolCallId: string | undefined): SubagentStep[] | undefined {
  const live = useChatContext().subagents ?? NO_SUBAGENTS;
  const get = useCallback(
    () => (toolCallId === undefined ? undefined : live.get(toolCallId)),
    [live, toolCallId],
  );

  return useSyncExternalStore(live.subscribe, get);
}
