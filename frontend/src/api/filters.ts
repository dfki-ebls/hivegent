import { z } from "zod";

/** A document or directory a selection names by its canonical path (see backend `FilterEntry`). */
const FilterEntrySchema = z.object({ path: z.string(), directory: z.boolean() });
export type FilterEntry = z.infer<typeof FilterEntrySchema>;

/** The documents a user message is about, which its metadata carries (see backend `DocumentSelection`). */
export const DocumentSelectionSchema = z.object({
  /** What the agent is pointed at, which restricts nothing. */
  included: z.array(FilterEntrySchema),
  /** What the agent's tools never reach. */
  excluded: z.array(FilterEntrySchema),
});
export type DocumentSelection = z.infer<typeof DocumentSelectionSchema>;
