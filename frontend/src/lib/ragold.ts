import { z } from "zod";

import { isWorkspacePath } from "@/api/documents";
import type { FetchedChunk, FetchedDocument } from "@/lib/chunks";
import { parseCitations, resolveCitation } from "@/lib/citations";
import { stripVoidTags } from "@/lib/normalize-void-tags";
import { chunksForDocument } from "@/stores/fetched-documents-store";

/** CRAG question types (Yang et al. 2024, https://doi.org/10.52202/079017-0335). */
export const QUESTION_TYPES = [
  "simple",
  "simple_w_condition",
  "comparison",
  "aggregation",
  "set",
  "multi-hop",
  "post-processing",
  "false_premise",
] as const;
type QuestionType = (typeof QUESTION_TYPES)[number];

/** MTRAG answerability (Katsis et al. 2025, arXiv 2501.03468). */
export const ANSWERABILITIES = ["answerable", "partial", "unanswerable"] as const;
export type Answerability = (typeof ANSWERABILITIES)[number];

export const PASSAGE_GRADES = ["needed", "helpful", "misleading"] as const;
export type PassageGrade = (typeof PASSAGE_GRADES)[number];

export const KEY_FACT_IMPORTANCES = ["must", "nice"] as const;
type KeyFactImportance = (typeof KEY_FACT_IMPORTANCES)[number];

/** A passage of a workspace document that bears on the answer. */
interface RagoldPassage {
  path: string;
  text: string;
  grade: PassageGrade;
}

/** A passage being edited, whose grade stays unset until the user picks it. */
export type RagoldDraftPassage = Omit<RagoldPassage, "grade"> & { grade: PassageGrade | null };

/** A verbatim excerpt of the response that a good answer has to contain. */
interface RagoldKeyFact {
  text: string;
  importance: KeyFactImportance;
}

/** One turn as a RAGold entry, the body of `POST /conversations/{id}/ragold` without its origin. */
export interface RagoldEntry {
  query: string;
  response: string;
  questionType: QuestionType;
  answerability: Answerability;
  notes: string;
  passages: RagoldPassage[];
  keyFacts: RagoldKeyFact[];
  /** The documents the archive ships, every passage path among them. */
  documents: string[];
}

export type RagoldRequest = RagoldEntry & { origin: string };

/** An entry being edited, whose classification and grades stay unset until the user picks them. */
export interface RagoldDraft extends Omit<
  RagoldEntry,
  "questionType" | "answerability" | "passages"
> {
  questionType: QuestionType | null;
  answerability: Answerability | null;
  passages: RagoldDraftPassage[];
}

/** *draft* as an entry once everything is filled in, or null while something is missing. */
export function completeEntry(draft: RagoldDraft): RagoldEntry | null {
  const { questionType, answerability } = draft;
  const passages = draft.passages.flatMap(({ grade, ...passage }) =>
    grade ? [{ ...passage, grade }] : [],
  );

  if (
    !draft.query.trim() ||
    !draft.response.trim() ||
    !questionType ||
    !answerability ||
    passages.length < draft.passages.length ||
    !draft.keyFacts.every((fact) => draft.response.includes(fact.text))
  ) {
    return null;
  }

  return { ...draft, questionType, answerability, passages };
}

/** *draft* with the document *path* added if missing. */
export function withDocument(draft: RagoldDraft, path: string): RagoldDraft {
  return draft.documents.includes(path)
    ? draft
    : { ...draft, documents: [...draft.documents, path] };
}

/** *draft* with *passage* added, and its document if missing. */
export function withPassage(draft: RagoldDraft, passage: RagoldDraftPassage): RagoldDraft {
  return withDocument({ ...draft, passages: [...draft.passages, passage] }, passage.path);
}

/** *draft* with *fact* added unless a key fact with its text exists. */
export function withKeyFact(draft: RagoldDraft, fact: RagoldKeyFact): RagoldDraft {
  return draft.keyFacts.some((item) => item.text === fact.text)
    ? draft
    : { ...draft, keyFacts: [...draft.keyFacts, fact] };
}

/**
 * The entry a reply prefills: its text without tags, and as passages the lines
 * each of its citations names, resolved from the captured tool outputs like a
 * citation chip resolves them, dropping citations without supporting output.
 */
export function draftFromTurn(
  query: string,
  reply: string,
  documents: ReadonlyMap<string, FetchedDocument>,
  chunks: ReadonlyMap<string, FetchedChunk>,
): RagoldDraft {
  let draft: RagoldDraft = {
    query: query.trim(),
    response: stripVoidTags(reply).trim(),
    questionType: null,
    answerability: null,
    notes: "",
    passages: [],
    keyFacts: [],
    documents: [],
  };

  const cites = Map.groupBy(parseCitations(reply), ({ src }) => src);

  for (const [src, group] of cites) {
    const document = src && isWorkspacePath(src) ? documents.get(src) : undefined;
    if (!src || !document) continue;

    const documentChunks = chunksForDocument(document, chunks);
    const texts = new Set(
      group.flatMap(({ line }) =>
        resolveCitation(documentChunks, line).flatMap(({ evidence }) =>
          evidence.map(({ text }) => text),
        ),
      ),
    );

    for (const text of texts) draft = withPassage(draft, { path: src, text, grade: null });
  }

  return draft;
}

const RagoldMessageSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("ragold:ready") }),
  z.object({
    type: z.literal("ragold:result"),
    ok: z.boolean(),
    count: z.number().optional(),
    error: z.string().optional(),
  }),
]);

/** Why a transfer failed after the archive was built: no answer, a closed tab, or a refused import. */
export class RagoldTransferError extends Error {
  constructor(
    readonly reason: "timeout" | "closed" | "rejected",
    message?: string,
  ) {
    super(message);
  }
}

const READY_TIMEOUT_MS = 30_000;
const CLOSED_POLL_MS = 500;

/**
 * Hand the archive *zip* resolves to over to RAGold in *tab*, once it reports
 * ready, and resolve with the number of entries it imported.
 *
 * Only messages from *tab* at *origin* count, and the tab closes when the
 * archive fails, since it would wait for nothing.
 */
export function sendToRagold(
  tab: Window,
  origin: string,
  zip: Promise<Blob>,
): Promise<number | undefined> {
  const { promise, resolve, reject } = Promise.withResolvers<number | undefined>();
  let ready = false;

  const onMessage = (event: MessageEvent<unknown>) => {
    if (event.source !== tab || event.origin !== origin) return;

    const message = RagoldMessageSchema.safeParse(event.data);
    if (!message.success) return;

    const { data } = message;

    if (data.type === "ragold:ready") {
      if (ready) return;

      ready = true;
      clearTimeout(timeout);
      void zip.then(
        (blob) => tab.postMessage({ type: "ragold:import", zip: blob }, origin),
        () => undefined,
      );
    } else if (data.ok) {
      finish(() => resolve(data.count));
    } else {
      finish(() => reject(new RagoldTransferError("rejected", data.error)));
    }
  };

  const timeout = setTimeout(
    () => finish(() => reject(new RagoldTransferError("timeout"))),
    READY_TIMEOUT_MS,
  );
  const poll = setInterval(() => {
    if (tab.closed) finish(() => reject(new RagoldTransferError("closed")));
  }, CLOSED_POLL_MS);

  function finish(settle: () => void) {
    window.removeEventListener("message", onMessage);
    clearTimeout(timeout);
    clearInterval(poll);
    settle();
  }

  window.addEventListener("message", onMessage);
  void zip.catch((error: unknown) => {
    tab.close();
    finish(() => reject(error));
  });

  return promise;
}
