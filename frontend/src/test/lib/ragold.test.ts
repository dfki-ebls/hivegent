import { beforeEach, describe, expect, it } from "vitest";

import {
  ANSWERABILITIES,
  completeEntry,
  draftFromTurn,
  KEY_FACT_IMPORTANCES,
  PASSAGE_GRADES,
  QUESTION_TYPES,
  type RagoldDraft,
} from "@/lib/ragold";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
// Vendored from RAGold, which the backend validates its archives against.
import ragoldSchema from "../../../../schemas/ragold.json";

const { Annotation, KeyFact, Passage } = ragoldSchema.$defs;

describe("draftFromTurn", () => {
  beforeEach(() => {
    const store = useFetchedDocumentsStore.getState();
    store.clearAll();
    store.markFullDocument("~/report.md", "Title\nBees fly.\nBees sting.", "read");
    store.addChunk({
      filename: "https://example.com",
      content: "Web",
      origin: "web",
      position: { type: "line", line: 1 },
    });
  });

  it("strips citations and resolves them to captured passages", () => {
    const { documents, chunks } = useFetchedDocumentsStore.getState();
    const reply =
      'Bees fly <cite src="~/report.md" line="2-3" />. Unread <cite src="~/other.md" line="1"/>, web <cite src="https://example.com" line="1"></cite>.';

    const draft = draftFromTurn(" What do bees do? ", reply, documents, chunks);

    expect(draft.query).toBe("What do bees do?");
    expect(draft.response).toBe("Bees fly. Unread, web.");
    expect(draft.passages).toEqual([
      { path: "~/report.md", text: "Bees fly.\nBees sting.", grade: null },
    ]);
    expect(draft.documents).toEqual(["~/report.md"]);
    expect(draft.questionType).toBeNull();
    expect(completeEntry(draft)).toBeNull();
  });
});

it("completes a draft once everything is picked and every key fact is in the response", () => {
  const draft: RagoldDraft = {
    query: "Q?",
    response: "Bees fly.",
    questionType: "simple",
    answerability: "answerable",
    notes: "",
    passages: [{ path: "~/a.md", text: "Bees fly.", grade: "needed" }],
    keyFacts: [{ text: "fly", importance: "must" }],
    documents: ["~/a.md"],
  };

  expect(completeEntry(draft)).toEqual(draft);
  expect(completeEntry({ ...draft, response: "Bees swim." })).toBeNull();
  expect(
    completeEntry({ ...draft, passages: [{ path: "~/a.md", text: "Bees fly.", grade: null }] }),
  ).toBeNull();
});

it("offers the question types, answerabilities, grades and importances RAGold accepts", () => {
  expect(Annotation.properties.questionType.enum).toEqual(QUESTION_TYPES);
  expect(Annotation.properties.answerability.enum).toEqual(ANSWERABILITIES);
  expect(Passage.properties.grade.enum).toEqual(PASSAGE_GRADES);
  expect(KeyFact.properties.importance.enum).toEqual(KEY_FACT_IMPORTANCES);
});
