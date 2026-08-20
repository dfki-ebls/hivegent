import { createContext, useContext } from "react";

/**
 * Who may answer the transcript's approval prompts, and whether right now.
 *
 * A pending approval is not stored as a decision but re-derived on every load —
 * a tool call with no result *is* an open approval — so the prompt reappears in
 * every tab that opens the conversation, long after the tab that asked is gone.
 * Answering it really runs the tool, so whether the buttons may be pressed is a
 * property of the session rather than of the part being rendered, which is why
 * it arrives by context instead of being threaded through the message tree.
 */
export interface ToolApprovalGate {
  /** Record the decision and let the run continue. */
  decide: (id: string, approved: boolean) => void;
  /** Set when the decision cannot be taken now; shown in place of the buttons. */
  blockedReason?: string;
}

const ToolApprovalContext = createContext<ToolApprovalGate>({ decide: () => {} });

export const ToolApprovalProvider = ToolApprovalContext.Provider;

export function useToolApproval(): ToolApprovalGate {
  return useContext(ToolApprovalContext);
}
