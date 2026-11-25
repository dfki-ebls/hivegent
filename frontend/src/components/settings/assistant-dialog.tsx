import { useMutation, useQuery } from "@tanstack/react-query";
import { PlusIcon, XIcon } from "lucide-react";
import { type SubmitEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type AssistantInfo, assistantMutations } from "@/api/assistants";
import { AGENT_MODES, type AgentMode } from "@/api/conversations";
import type { FilterEntry } from "@/api/filters";
import { isUsableSkill, skillQueries } from "@/api/skills";
import { toolQueries } from "@/api/tools";
import { DialogBody, ScrollDialogContent } from "@/components/scroll-dialog-content";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/toast";
import { keyPrefix } from "@/i18n";

const FORM_T = keyPrefix(($) => $.settings.assistants);

/** The assistant a dialog edits, or the scope a new one goes to. */
export type AssistantTarget = { scope: string; assistant?: AssistantInfo };

/** A text field's value, empty for none. */
function text(data: FormData, key: string): string {
  const value = data.get(key);

  return typeof value === "string" ? value : "";
}

/** The non-empty rows of an {@link EntryList} submitted under *name*. */
function entries(data: FormData, name: string): FilterEntry[] {
  const directories = new Set(data.getAll(`${name}.directory`).map(String));

  return data
    .getAll(`${name}.path`)
    .map((path, index) => ({
      path: typeof path === "string" ? path.trim() : "",
      directory: directories.has(String(index)),
    }))
    .filter(({ path }) => path);
}

/** Editable rows of a path and its directory toggle, submitted under *name*. */
function EntryList({
  legend,
  hint,
  name,
  initial,
}: {
  legend: string;
  hint: string;
  name: string;
  initial: readonly FilterEntry[];
}) {
  const { t } = useTranslation(undefined, FORM_T);
  const [rows, setRows] = useState(initial);
  const update = (index: number, row: FilterEntry) =>
    setRows(rows.map((other, at) => (at === index ? row : other)));

  return (
    <FieldSet>
      <FieldLegend variant="label">{legend}</FieldLegend>
      {rows.map((row, index) => (
        // Controlled rows, so their position is their identity.
        <Field key={index} orientation="horizontal">
          <Input
            name={`${name}.path`}
            aria-label={legend}
            className="font-mono"
            value={row.path}
            onChange={(event) => update(index, { ...row, path: event.target.value })}
          />
          <Checkbox
            id={`${name}-${index}`}
            name={`${name}.directory`}
            value={String(index)}
            checked={row.directory}
            onCheckedChange={(directory) => update(index, { ...row, directory })}
          />
          <FieldLabel htmlFor={`${name}-${index}`}>{t(($) => $.form.directory)}</FieldLabel>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t(($) => $.form.removePath)}
            onClick={() => setRows(rows.filter((_, at) => at !== index))}
          >
            <XIcon />
          </Button>
        </Field>
      ))}
      <Button
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => setRows([...rows, { path: "", directory: false }])}
      >
        <PlusIcon />
        {t(($) => $.form.addPath)}
      </Button>
      <FieldDescription>{hint}</FieldDescription>
    </FieldSet>
  );
}

const isMode = (value: string): value is AgentMode => AGENT_MODES.some((mode) => mode === value);

/** A group of checkboxes submitted under *name*, one per option. */
function Checklist({
  legend,
  name,
  options,
  checked,
}: {
  legend: string;
  name: string;
  options: readonly { value: string; label: string; title?: string }[];
  checked: readonly string[];
}) {
  if (options.length === 0) return null;

  return (
    <FieldSet>
      <FieldLegend variant="label">{legend}</FieldLegend>
      <div className="grid max-h-48 gap-2 overflow-y-auto sm:grid-cols-2">
        {options.map(({ value, label, title }) => (
          <Field key={value} orientation="horizontal">
            <Checkbox
              id={`${name}-${value}`}
              name={name}
              value={value}
              defaultChecked={checked.includes(value)}
            />
            <FieldLabel htmlFor={`${name}-${value}`} title={title} className="font-mono">
              {label}
            </FieldLabel>
          </Field>
        ))}
      </div>
    </FieldSet>
  );
}

/** Create or edit an assistant of a scope the user may change. */
export function AssistantDialog({
  target,
  open,
  onOpenChange,
}: {
  target: AssistantTarget | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation(undefined, FORM_T);
  const { t: tAll } = useTranslation();
  const { data: skills = [] } = useQuery(skillQueries.list());
  const { data: tools = [] } = useQuery(toolQueries.list());
  const save = useMutation(assistantMutations.save());
  const assistant = target?.assistant;

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!target) return;

    const data = new FormData(event.currentTarget);
    const mode = text(data, "max_mode");
    const name = assistant?.name ?? text(data, "name");

    save.mutate(
      {
        scope: target.scope,
        name,
        spec: {
          description: text(data, "description"),
          instructions: text(data, "instructions"),
          skills: data.getAll("skills").map(String),
          disabled_tools: data.getAll("disabled_tools").map(String),
          included_documents: entries(data, "included_documents"),
          excluded_documents: entries(data, "excluded_documents"),
          allowed_documents: entries(data, "allowed_documents"),
          max_mode: isMode(mode) ? mode : "write",
        },
      },
      {
        onSuccess: () => {
          toast.add({ type: "success", title: t(($) => $.saved, { name }) });
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <ScrollDialogContent className="sm:max-w-2xl">
        {/* Keyed by its target, so each opening starts from the assistant's values. */}
        <form
          key={`${target?.scope}/${assistant?.name ?? ""}`}
          onSubmit={submit}
          className="contents"
        >
          <DialogHeader>
            <DialogTitle>
              {assistant
                ? t(($) => $.form.editTitle, { name: assistant.name })
                : t(($) => $.form.createTitle)}
            </DialogTitle>
            <DialogDescription>{t(($) => $.form.description)}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <FieldGroup>
              {!assistant && (
                <Field>
                  <FieldLabel htmlFor="assistant-name">{t(($) => $.form.name)}</FieldLabel>
                  <Input
                    id="assistant-name"
                    name="name"
                    required
                    maxLength={64}
                    pattern="[a-z0-9]+(-[a-z0-9]+)*"
                  />
                  <FieldDescription>{t(($) => $.form.nameHint)}</FieldDescription>
                </Field>
              )}
              <Field>
                <FieldLabel htmlFor="assistant-description">{t(($) => $.form.summary)}</FieldLabel>
                <Input
                  id="assistant-description"
                  name="description"
                  maxLength={1024}
                  defaultValue={assistant?.description}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor="assistant-instructions">
                  {t(($) => $.form.instructions)}
                </FieldLabel>
                <Textarea
                  id="assistant-instructions"
                  name="instructions"
                  required
                  rows={8}
                  maxLength={20_000}
                  defaultValue={assistant?.instructions}
                />
                <FieldDescription>{t(($) => $.form.instructionsHint)}</FieldDescription>
              </Field>
              <Field>
                <FieldLabel htmlFor="assistant-mode">{t(($) => $.form.maxMode)}</FieldLabel>
                <NativeSelect
                  id="assistant-mode"
                  name="max_mode"
                  defaultValue={assistant?.max_mode ?? "write"}
                >
                  {AGENT_MODES.map((mode) => (
                    <NativeSelectOption key={mode} value={mode}>
                      {tAll(($) => $.options.mode[mode])}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
              </Field>
              <Checklist
                legend={t(($) => $.form.skills)}
                name="skills"
                options={skills.filter(isUsableSkill).map((skill) => ({
                  value: skill.ref,
                  label: skill.ref,
                  title: skill.description ?? undefined,
                }))}
                checked={assistant?.skills ?? []}
              />
              <Checklist
                legend={t(($) => $.form.disabledTools)}
                name="disabled_tools"
                options={tools.map((tool) => ({
                  value: tool.name,
                  label: tool.name,
                  title: tool.description,
                }))}
                checked={assistant?.disabled_tools ?? []}
              />
              {(
                [
                  ["included_documents", t(($) => $.form.includedDocuments)],
                  ["excluded_documents", t(($) => $.form.excludedDocuments)],
                  ["allowed_documents", t(($) => $.form.allowedDocuments)],
                ] as const
              ).map(([key, label]) => (
                <EntryList
                  key={key}
                  legend={label}
                  hint={
                    key === "allowed_documents"
                      ? t(($) => $.form.allowedDocumentsHint)
                      : t(($) => $.form.documentsHint)
                  }
                  name={key}
                  initial={assistant?.[key] ?? []}
                />
              ))}
            </FieldGroup>
          </DialogBody>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              {t(($) => $.form.cancel)}
            </DialogClose>
            <Button type="submit" disabled={save.isPending}>
              {t(($) => $.form.save)}
            </Button>
          </DialogFooter>
        </form>
      </ScrollDialogContent>
    </Dialog>
  );
}
