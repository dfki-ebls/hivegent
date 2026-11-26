import { useMutation, useQuery } from "@tanstack/react-query";
import { type SubmitEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type SkillInfo, skillMutations, skillQueries } from "@/api/skills";
import { DialogBody, ScrollDialogContent } from "@/components/scroll-dialog-content";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/toast";
import { keyPrefix } from "@/i18n";

const FORM_T = keyPrefix(($) => $.settings.skills);

/** The skill a dialog shows, or the scope a new one goes to. */
export type SkillTarget = { skill: SkillInfo } | { scope: string };

interface SkillDialogProps {
  target: SkillTarget | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Write a new skill's `SKILL.md`, edit a writable one's, or read any other's. */
export function SkillDialog({ target, open, onOpenChange }: SkillDialogProps) {
  const close = () => onOpenChange(false);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <ScrollDialogContent className="sm:max-w-3xl">
        {/* Keyed by its target, so each opening starts from the skill's file. */}
        {target &&
          ("skill" in target ? (
            <LoadedSkillForm key={target.skill.path} skill={target.skill} onSaved={close} />
          ) : (
            <SkillForm key={target.scope} scope={target.scope} onSaved={close} />
          ))}
      </ScrollDialogContent>
    </Dialog>
  );
}

/** The form of an existing *skill*, once its `SKILL.md` loaded, or from the template where it holds none. */
function LoadedSkillForm({ skill, onSaved }: { skill: SkillInfo; onSaved: () => void }) {
  const { data: content, isError } = useQuery(skillQueries.document(skill));

  if (content === undefined && !isError) {
    return (
      <>
        <DialogHeader>
          <DialogTitle>{skill.name}</DialogTitle>
        </DialogHeader>
        <DialogBody className="items-center">
          <Spinner />
        </DialogBody>
      </>
    );
  }

  return <SkillForm scope={skill.scope} skill={skill} content={content} onSaved={onSaved} />;
}

/** The `SKILL.md` a new skill named *name* starts from. */
function template(name: string, description: string, body: string): string {
  return `---\nname: ${name}\ndescription: ${description}\n---\n\n${body}\n`;
}

/** The form of the skill *skill*, or of a new one in the library of *scope*. */
function SkillForm({
  scope,
  skill,
  content,
  onSaved,
}: {
  scope: string;
  skill?: SkillInfo;
  content?: string;
  onSaved: () => void;
}) {
  const { t } = useTranslation(undefined, FORM_T);
  const save = useMutation(skillMutations.saveDocument());
  const [name, setName] = useState(skill?.name ?? "");
  // A new skill's file follows its name until the user edits the file itself.
  const [edited, setEdited] = useState(content);
  const readOnly = skill !== undefined && !skill.writable;
  const value =
    edited ??
    template(
      name || "my-skill",
      t(($) => $.form.templateDescription),
      t(($) => $.form.templateBody),
    );

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();

    save.mutate(
      { scope, name, content: value, create: skill === undefined },
      {
        onSuccess: (saved) => {
          toast.add({ type: "success", title: t(($) => $.saved, { name: saved.name }) });
          onSaved();
        },
      },
    );
  }

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>
          {skill
            ? readOnly
              ? skill.name
              : t(($) => $.form.editTitle, { name: skill.name })
            : t(($) => $.form.createTitle)}
        </DialogTitle>
        {!readOnly && <DialogDescription>{t(($) => $.form.description)}</DialogDescription>}
      </DialogHeader>
      <DialogBody>
        <FieldGroup>
          {!skill && (
            <Field>
              <FieldLabel htmlFor="skill-name">{t(($) => $.form.name)}</FieldLabel>
              <Input
                id="skill-name"
                required
                maxLength={64}
                pattern="[a-z0-9]+(-[a-z0-9]+)*"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <FieldDescription>{t(($) => $.form.nameHint)}</FieldDescription>
            </Field>
          )}
          <Field>
            <FieldLabel htmlFor="skill-content">{t(($) => $.form.content)}</FieldLabel>
            <Textarea
              id="skill-content"
              className="font-mono text-xs"
              required
              readOnly={readOnly}
              rows={20}
              value={value}
              onChange={(event) => setEdited(event.target.value)}
            />
          </Field>
        </FieldGroup>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" />}>
          {readOnly ? t(($) => $.form.close) : t(($) => $.form.cancel)}
        </DialogClose>
        {!readOnly && (
          <Button type="submit" disabled={save.isPending}>
            {t(($) => $.form.save)}
          </Button>
        )}
      </DialogFooter>
    </form>
  );
}
