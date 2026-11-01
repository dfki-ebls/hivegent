/**
 * Subagent transcript: a coarse, ordered timeline of what a delegating tool's
 * subagent did, that it reasoned, replied, or called a tool, without the contents.
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
 * Steps are label-only and append-only, so a live snapshot is the current
 * transcript and the frontend keeps the latest.
 */

import { z } from "zod";

/** One coarse action the subagent took, `tool_name` is set only for tool steps. */
const SubagentStepSchema = z.object({
  kind: z.enum(["reasoning", "message", "tool"]),
  tool_name: z.string().optional(),
});
export type SubagentStep = z.infer<typeof SubagentStepSchema>;

const SubagentTranscriptSchema = z.object({
  transcript: z.literal("subagent"),
  steps: z.array(SubagentStepSchema),
});
type SubagentTranscript = z.infer<typeof SubagentTranscriptSchema>;

/** A live transcript snapshot addressed to its parent tool-call id. */
export const SubagentUpdateSchema = z.object({
  tool_call_id: z.string(),
  transcript: SubagentTranscriptSchema,
});
type SubagentUpdate = z.infer<typeof SubagentUpdateSchema>;

export function isSubagentTranscript(value: unknown): value is SubagentTranscript {
  return SubagentTranscriptSchema.safeParse(value).success;
}

/**
 * The live transcripts of one chat, keyed by the parent tool-call id.
 * Transient parts never reach `message.parts`, so this is their only live source.
 */
export class SubagentLive {
  #steps = new Map<string, SubagentStep[]>();
  #listeners = new Set<() => void>();

  update = ({ tool_call_id, transcript }: SubagentUpdate): void => {
    this.#steps.set(tool_call_id, transcript.steps);

    for (const listener of this.#listeners) listener();
  };

  get = (toolCallId: string): SubagentStep[] | undefined => this.#steps.get(toolCallId);

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);

    return () => this.#listeners.delete(listener);
  };
}
