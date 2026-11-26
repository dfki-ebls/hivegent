import { useMutation, useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import type { SubmitEvent } from "react";
import { useTranslation } from "react-i18next";

import { memoryMutations, memoryQueries } from "@/api/account";
import { MAX_INSTRUCTIONS, preferenceMutations, preferenceQueries } from "@/api/preferences";
import { PageHeader } from "@/components/page-header";
import { Section } from "@/components/settings/section";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/toast";
import { keyPrefix } from "@/i18n";

const INSTRUCTIONS_T = keyPrefix(($) => $.settings.instructions);

export const Route = createFileRoute("/settings/instructions")({
  loader: ({ context }) =>
    Promise.all([
      context.queryClient.query(preferenceQueries.get()),
      context.queryClient.query(memoryQueries.get()),
    ]),
  component: InstructionsPage,
});

/** The user's own instructions, which every chat follows, and the memory the assistant keeps of them. */
function InstructionsPage() {
  const { t } = useTranslation(undefined, INSTRUCTIONS_T);
  const { data: preferences } = useSuspenseQuery(preferenceQueries.get());
  const { data: memory } = useSuspenseQuery(memoryQueries.get());
  const saveInstructions = useMutation(preferenceMutations.set());
  const saveMemory = useMutation(memoryMutations.save());

  return (
    <>
      <PageHeader title={t(($) => $.title)} description={t(($) => $.description)} />
      <Section>
        <TextForm
          id="instructions"
          label={t(($) => $.label)}
          hint={t(($) => $.hint)}
          save={t(($) => $.save)}
          initial={preferences.instructions}
          maxLength={MAX_INSTRUCTIONS}
          pending={saveInstructions.isPending}
          onSave={(instructions) =>
            saveInstructions.mutate(
              { instructions },
              { onSuccess: () => toast.add({ type: "success", title: t(($) => $.saved) }) },
            )
          }
        />
      </Section>
      <Section title={t(($) => $.memory.title)} description={t(($) => $.memory.description)}>
        <TextForm
          id="memory"
          label={t(($) => $.memory.label)}
          save={t(($) => $.memory.save)}
          initial={memory.content}
          pending={saveMemory.isPending}
          onSave={(content) =>
            saveMemory.mutate(content, {
              onSuccess: () => toast.add({ type: "success", title: t(($) => $.memory.saved) }),
            })
          }
        />
      </Section>
    </>
  );
}

interface TextFormProps {
  id: string;
  label: string;
  hint?: string;
  save: string;
  initial: string;
  maxLength?: number;
  pending: boolean;
  onSave: (text: string) => void;
}

/** One text the user edits and saves whole. */
function TextForm({ id, label, hint, save, initial, maxLength, pending, onSave }: TextFormProps) {
  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = new FormData(event.currentTarget).get(id);

    onSave(typeof text === "string" ? text : "");
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field>
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        <Textarea id={id} name={id} rows={12} maxLength={maxLength} defaultValue={initial} />
        {hint && <FieldDescription>{hint}</FieldDescription>}
      </Field>
      <Button type="submit" className="self-end" disabled={pending}>
        {save}
      </Button>
    </form>
  );
}
