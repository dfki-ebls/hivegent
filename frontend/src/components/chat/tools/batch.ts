import { z } from "zod";

/** Mirrors `ItemFailure` in `backend/src/hivegent/tools/base.py`, an item a batch could not serve and why. */
const ItemFailureSchema = z.object({
  item: z.string(),
  reason: z.string(),
  kind: z.literal("failure"),
});
type ItemFailure = z.infer<typeof ItemFailureSchema>;

/** A `Batch[R]` payload, split into its results and its failures, each in request order. */
interface Batch<T> {
  results: T[];
  failures: ItemFailure[];
}

function isFailure<T extends object>(entry: T | ItemFailure): entry is ItemFailure {
  return "kind" in entry && entry.kind === "failure";
}

/** Parse a `Batch[R]` payload of *schema* results, empty when the payload has another shape. */
export function parseBatch<T extends object>(metadata: unknown, schema: z.ZodType<T>): Batch<T> {
  const entries = z.array(z.union([ItemFailureSchema, schema])).safeParse(metadata).data ?? [];

  return {
    results: entries.filter((entry): entry is T => !isFailure(entry)),
    failures: entries.filter(isFailure),
  };
}

/**
 * A list argument as the backend accepts it, where one bare item stands for a list of one
 * (`accept_scalar` in `backend/src/hivegent/tools/base.py`).
 */
export function listArgument<T extends z.ZodType>(item: T) {
  return z.array(item).or(item.transform((value) => [value]));
}
