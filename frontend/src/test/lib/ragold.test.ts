import { beforeEach, describe, expect, it } from "vitest";

import {
  ANSWERABILITIES,
  draftFromTurn,
  PASSAGE_GRADES,
  QUESTION_TYPES,
} from "@/lib/ragold";
import { useFetchedDocumentsStore } from "@/stores/fetched-documents-store";
// Vendored from RAGold, which the backend validates its archives against.
import ragoldSchema from "../../../../backend/tests/fixtures/ragold-schema.json";

const { Annotation, Passage } = ragoldSchema.$defs;

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
      { path: "~/report.md", text: "Bees fly.\nBees sting.", grade: "needed" },
    ]);
    expect(draft.documents).toEqual(["~/report.md"]);
  });
});

it("offers the question types, answerabilities and grades RAGold accepts", () => {
  expect(Annotation.properties.questionType.enum).toEqual(QUESTION_TYPES);
  expect(Annotation.properties.answerability.enum).toEqual(ANSWERABILITIES);
  expect(Passage.properties.grade.enum).toEqual(PASSAGE_GRADES);
});
