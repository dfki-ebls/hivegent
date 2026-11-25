import { useRef } from "react";
import { useTranslation } from "react-i18next";

import { Field, FieldDescription, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { type RagoldDraft, withKeyFact } from "@/lib/ragold";
import {
  T_OPTIONS,
  useOptions,
  useTextSelection,
  AddButtons,
  ExcerptRow,
} from "@/components/chat/ragold/shared";

/** Key facts marked by selecting text of the response, each with its importance. */
export function KeyFactsField({
  draft,
  setDraft,
}: {
  draft: RagoldDraft;
  setDraft: (update: (prev: RagoldDraft) => RagoldDraft) => void;
}) {
  const { t } = useTranslation(undefined, T_OPTIONS);
  const options = useOptions();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const selection = useTextSelection(textarea);
  const updateKeyFacts = (update: (keyFacts: RagoldDraft["keyFacts"]) => RagoldDraft["keyFacts"]) =>
    setDraft((prev) => ({ ...prev, keyFacts: update(prev.keyFacts) }));

  return (
    <>
      <Field>
        <FieldLabel htmlFor="ragold-response">{t(($) => $.response)}</FieldLabel>
        <Textarea
          ref={textarea}
          id="ragold-response"
          value={draft.response}
          onChange={(event) => setDraft((prev) => ({ ...prev, response: event.target.value }))}
          onSelect={selection.onSelect}
          className="max-h-64"
        />
      </Field>
      <FieldSet>
        <FieldLegend variant="label">{t(($) => $.keyFacts)}</FieldLegend>
        <FieldDescription>{t(($) => $.keyFactsDescription)}</FieldDescription>
        <div className="flex flex-wrap gap-2">
          <AddButtons
            options={options.importances}
            disabled={!selection.text}
            onAdd={(importance) => {
              setDraft((prev) => withKeyFact(prev, { text: selection.text, importance }));
              selection.clear();
            }}
          />
        </div>
        {draft.keyFacts.map((fact) => (
          <ExcerptRow
            key={fact.text}
            text={fact.text}
            value={fact.importance}
            options={options.importances}
            label={t(($) => $.importance)}
            removeLabel={t(($) => $.removeKeyFact)}
            error={draft.response.includes(fact.text) ? undefined : t(($) => $.staleKeyFact)}
            onChange={(importance) =>
              updateKeyFacts((prev) =>
                prev.map((item) => (item === fact ? { ...item, importance } : item)),
              )
            }
            onRemove={() => updateKeyFacts((prev) => prev.filter((item) => item !== fact))}
          />
        ))}
      </FieldSet>
    </>
  );
}
