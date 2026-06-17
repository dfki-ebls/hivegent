/**
 * Subagent transcript: a coarse, ordered timeline of what a delegating tool's
 * subagent did (`explore` today, others later) — that it reasoned, replied, or
 * called a tool, without the contents.
 *
 * The backend surfaces this two ways onto one shape (see
 * `backend/.../agents/subagent_events.py`):
 *
 * - live, as transient `data-subagent` parts carrying a whole {@link SubagentUpdate}
 *   snapshot streamed while the subagent runs, so a long run never looks stuck.
 * - persisted, as the final {@link SubagentTranscript} carried on the tool's
 *   `data-tool-output` metadata (tagged `transcript: "subagent"` so it is
 *   recognised generically, not by tool name), so it survives a reload.
 *
 * Steps are label-only and append-only, so a live snapshot is just the current
 * transcript — no incremental diff protocol, the frontend keeps the latest.
 */

/** One coarse action the subagent took; `tool_name` is set only for tool steps. */
export interface SubagentStep {
  kind: "reasoning" | "message" | "tool";
  tool_name?: string;
}

export interface SubagentTranscript {
  transcript: "subagent";
  steps: SubagentStep[];
}

/** A live transcript snapshot addressed to its parent tool-call id. */
export interface SubagentUpdate {
  tool_call_id: string;
  transcript: SubagentTranscript;
}

/** Live transcripts keyed by the parent `explore` tool-call id. */
export type SubagentSteps = ReadonlyMap<string, SubagentStep[]>;

export function isSubagentTranscript(value: unknown): value is SubagentTranscript {
  return (
    value != null &&
    typeof value === "object" &&
    (value as { transcript?: unknown }).transcript === "subagent"
  );
}
