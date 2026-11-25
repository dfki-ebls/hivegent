import { useMutation, useSuspenseQuery } from "@tanstack/react-query";
import { FileArchiveIcon, FolderUpIcon } from "lucide-react";
import { type ChangeEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { keyPrefix } from "@/i18n";
import { skillFile, skillMutations, skillQueries } from "@/api/skills";
import { buildCollectionZipFromDirectoryInput, directoryInputName } from "@/lib/collection-upload";
import { DocumentDialog } from "@/components/document-dialog";
import { LibrarySections } from "@/components/settings/library-sections";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toast";

const SKILLS_T = keyPrefix(($) => $.settings.skills);

/** Upload a skill to the library of *scope*, as a ZIP archive or a folder zipped here. */
function SkillUpload({ scope }: { scope: string }) {
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
    <div className="flex gap-2">
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
 * The skills of every scope the user sees, one card each, uploads into the
 * libraries they may change, or with *global* only the operator's.
 */
export function SkillsSection({ global = false }: { global?: boolean }) {
  const { t } = useTranslation(undefined, SKILLS_T);
  const { data: skills } = useSuspenseQuery(skillQueries.list());
  const [opened, setOpened] = useState<string | null>(null);

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
              onClick={() => setOpened(skillFile(skill))}
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
        renderLibrary={(scope) => <SkillUpload scope={scope} />}
      />
      {opened !== null && (
        <DocumentDialog open onOpenChange={(open) => !open && setOpened(null)} filename={opened} />
      )}
    </>
  );
}
