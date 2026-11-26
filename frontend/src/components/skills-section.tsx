import { useMutation, useSuspenseQuery } from "@tanstack/react-query";
import { FileArchiveIcon, FolderUpIcon, PlusIcon } from "lucide-react";
import { type ChangeEvent, useRef } from "react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { skillMutations, skillQueries } from "@/api/skills";
import { buildCollectionZipFromDirectoryInput, directoryInputName } from "@/lib/collection-upload";
import { LibrarySections } from "@/components/settings/library-sections";
import { SkillDialog, type SkillTarget } from "@/components/settings/skill-dialog";
import { useDialogTarget } from "@/hooks/use-dialog-target";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";

const SKILLS_T = keyPrefix(($) => $.settings.skills);

/** Write a new skill into the library of *scope*, or upload one as a ZIP archive or a folder zipped here. */
function SkillLibraryActions({ scope, onCreate }: { scope: string; onCreate: () => void }) {
  const { t } = useTranslation(undefined, SKILLS_T);
  const archiveRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const install = useMutation({
    ...skillMutations.install(),
    onSuccess: ({ skill, scripts }) => {
      const failing = scripts.filter((script) => script.problem !== null).length;

      toast.add({
        type: failing ? "warning" : "success",
        title: t(($) => $.installed, { name: skill.name }),
        description: failing ? t(($) => $.notRunnable, { count: failing }) : undefined,
      });
    },
  });

  async function upload(event: ChangeEvent<HTMLInputElement>, folder: boolean) {
    const files = [...(event.target.files ?? [])];
    event.target.value = "";

    if (files.length === 0) return;

    const archive = folder
      ? await buildCollectionZipFromDirectoryInput(files, `${directoryInputName(files)}.zip`)
      : files[0];

    if (archive) install.mutate({ scope, archive });
  }

  return (
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" size="sm" onClick={onCreate}>
        <PlusIcon data-icon="inline-start" />
        {t(($) => $.create)}
      </Button>
      <input
        ref={archiveRef}
        type="file"
        accept=".zip,application/zip"
        className="hidden"
        aria-label={t(($) => $.uploadArchive)}
        onChange={(event) => void upload(event, false)}
      />
      <input
        ref={folderRef}
        type="file"
        webkitdirectory=""
        multiple
        className="hidden"
        aria-label={t(($) => $.uploadFolder)}
        onChange={(event) => void upload(event, true)}
      />
      <Button
        variant="outline"
        size="sm"
        disabled={install.isPending}
        onClick={() => archiveRef.current?.click()}
      >
        <FileArchiveIcon data-icon="inline-start" />
        {t(($) => $.uploadArchive)}
      </Button>
      <Button
        variant="outline"
        size="sm"
        disabled={install.isPending}
        onClick={() => folderRef.current?.click()}
      >
        <FolderUpIcon data-icon="inline-start" />
        {t(($) => $.uploadFolder)}
      </Button>
    </div>
  );
}

/**
 * The skills of every scope the user sees, one card each, each opened in a
 * dialog that edits its `SKILL.md` where the user may, new ones written or
 * uploaded into the libraries they may change, or with *global* only the operator's.
 */
export function SkillsSection({ global = false }: { global?: boolean }) {
  const { t } = useTranslation(undefined, SKILLS_T);
  const { data: skills } = useSuspenseQuery(skillQueries.list());
  const dialog = useDialogTarget<SkillTarget | null>(null);

  return (
    <>
      <LibrarySections
        entries={skills}
        global={global}
        remove={skillMutations.delete()}
        labels={{
          empty: t(($) => $.empty),
          delete: t(($) => $.delete.label),
          deleteTitle: ({ name }) => t(($) => $.delete.title, { name }),
          deleteDescription: t(($) => $.delete.description),
        }}
        entryKey={(skill) => skill.path}
        renderEntry={(skill) => (
          <>
            <Button
              variant="link"
              className="h-auto p-0 font-mono"
              disabled={skill.problem !== null && !skill.writable}
              onClick={() => dialog.show({ skill })}
            >
              {skill.name}
            </Button>
            <p
              className={
                skill.problem ? "text-destructive text-sm" : "text-muted-foreground text-sm"
              }
            >
              {skill.problem ?? skill.description}
            </p>
            {skill.warnings.map((note) => (
              <p key={note} className="text-muted-foreground text-xs">
                {note}
              </p>
            ))}
          </>
        )}
        renderLibrary={(scope) => (
          <SkillLibraryActions scope={scope} onCreate={() => dialog.show({ scope })} />
        )}
      />
      <SkillDialog target={dialog.target} open={dialog.open} onOpenChange={dialog.setOpen} />
    </>
  );
}
