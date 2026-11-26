import type { UIMessage } from "ai";
import { z } from "zod";

import { DocumentSelectionSchema } from "@/api/filters";

/**
 * UI-owned metadata the backend persists on a message and the client reads back
 * on reload (see the `*_KEY` constants in `backend/src/hivegent/server/vercel/adapter.py`).
 */
const ChatMessageMetadataSchema = z.object({
  /** How long each reasoning part streamed, keyed by run in turn order. */
  reasoningDurationsMs: z.record(z.string(), z.array(z.number())).optional(),
  chatError: z.string().optional(),
  /** What each pending call's `ApprovalRequired` carried, keyed by tool call id. */
  approvalMetadata: z.record(z.string(), z.unknown()).optional(),
  /** The arguments each pending call runs with once approved, keyed by tool call id. */
  approvalArgs: z.record(z.string(), z.record(z.string(), z.unknown())).optional(),
  /** On a user message, the documents it is about, which a regenerate or compaction answers with again. */
  documents: DocumentSelectionSchema.partial().optional(),
});
type ChatMessageMetadata = z.infer<typeof ChatMessageMetadataSchema>;

/**
 * The `data-*` parts the backend streams, keyed by name without the prefix.
 * Mirrors `CLIENT_DATA_TYPES` in `backend/src/hivegent/server/vercel/transcript.py`, which withholds every other one.
 *
 * `compaction` carries the summary that replaced the history before it, live and after a reload.
 * Its data may hold more keys, but only `content` is meant for display.
 * `turn` carries the stored version of the turn that just ended, validated where it is swapped in.
 */
const chatDataSchemas = {
  compaction: z.object({ content: z.string() }),
  "tool-output": z.unknown(),
  turn: z.array(z.unknown()),
};

type ChatDataTypes = {
  [K in keyof typeof chatDataSchemas]: z.infer<(typeof chatDataSchemas)[K]>;
};

/** The message type used throughout the chat, carrying its metadata and data part shapes. */
export type ChatMessage = UIMessage<ChatMessageMetadata, ChatDataTypes>;

/** What a stored or streamed message is validated with, tool parts stay untyped. */
export const messageSchemas = {
  // User messages and earlier responses carry none, which the backend sends as null.
  metadataSchema: ChatMessageMetadataSchema.nullish().transform(
    (metadata) => metadata ?? undefined,
  ),
  dataSchemas: chatDataSchemas,
};
