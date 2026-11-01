import { type ChangeEvent, createContext, useContext, useMemo, useRef, useState } from "react";
import type { z } from "zod";
import { useTranslation } from "react-i18next";
import { useChatContext, useMessageLive } from "@/components/chat/chat-context";

/** The props binding a note field, uncontrolled so typing re-renders nothing but the field. */
interface NoteField {
  key: string;
  defaultValue: string;
  onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void;
}

/**
 * The unanswered requests of one message and what the user has entered for them.
 *
 * A lone request is answered from its card at once. Several are staged from
 * their cards and sent together, since the run continues only once every
 * request is answered.
 */
export interface ApprovalRound {
  /** The request ids, in transcript order. */
  ids: readonly string[];
  /** Staged choices by request id. */
  choices: Readonly<Record<string, boolean>>;
  stage: (id: string, approved: boolean) => void;
  /** Bind the note of the request *id*, or the note for every request of the round when omitted. */
  noteField: (id?: string) => NoteField;
  /** Answer every request with *approved*, or with its staged choice when omitted. */
  send: (approved?: boolean) => void;
}

/** Why the requests of the message being rendered cannot be answered yet, which is while its turn streams. */
export function useApprovalBlockedReason(): string | undefined {
  const { t } = useTranslation();

  return useMessageLive() === "streaming" ? t(($) => $.chat.sidebar.approvalBlocked) : undefined;
}

/** The notes that apply to a call, or none when the user wrote neither. */
function joinNotes(...notes: (string | undefined)[]): string | undefined {
  return (
    notes
      .map((note) => note?.trim())
      .filter(Boolean)
      .join("\n\n") || undefined
  );
}

function bindNote(notes: Map<string, string>, key: string): NoteField {
  return {
    key,
    defaultValue: notes.get(key) ?? "",
    onChange: (event) => notes.set(key, event.target.value),
  };
}

/**
 * Stage decisions for the *pending* requests.
 * Requests that overlap belong to the same round, even when more arrive earlier.
 * The notes are handed over on sending rather than held in state,
 * and the round keeps its identity until the requests or choices change.
 *
 * The SDK records decisions one at a time and continues the run once the last
 * one is in, so a batch still sends a single continuation. The reason is the
 * user's own note, and the backend words the refusal the model reads around it.
 */
export function useApprovalRoundState(pending: readonly string[]): ApprovalRound {
  const { chat } = useChatContext();
  const key = pending.join("\n");
  // The caller derives the ids anew on every streaming render, their key stays put.
  const ids = useMemo(() => (key ? key.split("\n") : []), [key]);
  const [choices, setChoices] = useState<Readonly<Record<string, boolean>>>({});
  const notes = useRef(new Map<string, string>()).current;
  const sharedNotes = useRef(new Map<string, string>()).current;
  const [round, setRound] = useState({ ids, key: ids[0] ?? "" });

  if (round.ids !== ids) {
    setRound({
      ids,
      key: ids.some((id) => round.ids.includes(id)) ? round.key : (ids[0] ?? ""),
    });
  }

  return useMemo(
    () => ({
      ids,
      choices,
      stage: (id, approved) => setChoices((prev) => ({ ...prev, [id]: approved })),
      noteField: (id) => bindNote(id === undefined ? sharedNotes : notes, id ?? round.key),
      send: (approved) => {
        for (const id of ids) {
          const choice = approved ?? choices[id];
          const reason = joinNotes(sharedNotes.get(round.key), notes.get(id));

          if (choice === undefined) continue;

          void chat.addToolApprovalResponse(
            reason ? { id, approved: choice, reason } : { id, approved: choice },
          );
        }
      },
    }),
    [ids, round.key, choices, chat, notes, sharedNotes],
  );
}

const ApprovalRoundContext = createContext<ApprovalRound | undefined>(undefined);

/** `ApprovalScope` provides its message's round, so the cards of its pending calls can answer. */
export const ApprovalRoundProvider = ApprovalRoundContext.Provider;

export function useApprovalRound(): ApprovalRound | undefined {
  return useContext(ApprovalRoundContext);
}

/**
 * What each approval-gated call of one message asked with, keyed by tool call id.
 *
 * The protocol's approval request carries ids only, so the backend sends the
 * metadata of every `ApprovalRequired` as message metadata (`APPROVAL_METADATA_KEY`
 * in `backend/src/hivegent/server/vercel.py`) and stores it with the response,
 * so the prompt shows the same server-resolved details live and after a reload.
 * It is a context apart from the round, so staging a choice leaves the other cards be.
 */
const ApprovalMetadataContext = createContext<Readonly<Record<string, unknown>> | undefined>(
  undefined,
);

/** `ApprovalScope` provides its message's metadata. */
export const ApprovalMetadataProvider = ApprovalMetadataContext.Provider;

/**
 * The approval metadata of *part*'s call when it matches *schema*.
 * Undefined for a call that asked nobody, or whose metadata has another shape.
 */
export function useApprovalMetadata<T>(
  part: { toolCallId?: string },
  schema: z.ZodType<T>,
): T | undefined {
  const byCallId = useContext(ApprovalMetadataContext);
  const metadata = part.toolCallId ? byCallId?.[part.toolCallId] : undefined;

  return useMemo(() => schema.safeParse(metadata).data, [schema, metadata]);
}
