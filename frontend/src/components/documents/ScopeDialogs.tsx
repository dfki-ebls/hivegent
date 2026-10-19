import { useMutation } from "@tanstack/react-query";
import { type Ref, useImperativeHandle } from "react";
import { useTranslation } from "react-i18next";

import { useDialogTarget } from "@/hooks/use-dialog-target";
import { documentMutations } from "@/queries/documents";
import { basename, parentDir } from "@/lib/utils";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { NameInputDialog } from "@/components/documents/NameInputDialog";

type DeleteTarget =
  | { kind: "file"; path: string }
  | { kind: "directory"; path: string }
  | { kind: "bulk"; files: string[] };

type RenameTarget = { kind: "file" | "directory"; path: string };

/** Imperative openers exposed to the owning ScopeSection. */
export interface ScopeDialogsHandle {
  deleteFile: (path: string) => void;
  deleteDir: (path: string) => void;
  bulkDelete: (files: string[]) => void;
  renameFile: (path: string) => void;
  renameDir: (path: string) => void;
}

interface ScopeDialogsProps {
  ref?: Ref<ScopeDialogsHandle>;
  /** Workspace scope: `~` for personal, `@<group>` for a group. */
  scope: string;
  /** Run after a bulk delete starts (e.g. to clear the selection). */
  onBulkDone: () => void;
}

/**
 * The delete confirmation and rename dialogs for one scope. Owns their state and
 * runs the matching mutations, so ScopeSection only has to call the
 * imperative openers from its tree and bulk actions. A rename is a same-directory
 * move, so it reuses the move mutation. Each target outlives its dialog's
 * close, so the dialog keeps its text while it animates out.
 */
export function ScopeDialogs({ ref, scope, onBulkDone }: ScopeDialogsProps) {
  const { t } = useTranslation();
  const remove = useMutation(documentMutations.remove());
  const move = useMutation(documentMutations.move());

  const deleteDialog = useDialogTarget<DeleteTarget | null>(null);
  const renameDialog = useDialogTarget<RenameTarget | null>(null);
  const pendingDelete = deleteDialog.target;
  const pendingRename = renameDialog.target;
  const showDelete = deleteDialog.show;
  const showRename = renameDialog.show;

  useImperativeHandle(
    ref,
    () => ({
      deleteFile: (path) => showDelete({ kind: "file", path }),
      deleteDir: (path) => showDelete({ kind: "directory", path }),
      bulkDelete: (files) => showDelete({ kind: "bulk", files }),
      renameFile: (path) => showRename({ kind: "file", path }),
      renameDir: (path) => showRename({ kind: "directory", path }),
    }),
    [showDelete, showRename],
  );

  const confirmDelete = () => {
    if (!pendingDelete) return;

    deleteDialog.setOpen(false);

    if (pendingDelete.kind === "bulk") onBulkDone();

    remove.mutate(
      pendingDelete.kind === "bulk"
        ? { scope, paths: pendingDelete.files, expect: "entry" }
        : {
            scope,
            paths: [pendingDelete.path],
            expect: pendingDelete.kind === "directory" ? "dir" : "entry",
          },
    );
  };

  // A rename keeps the entry's parent directory and swaps its basename, which is
  // exactly a same-scope move to the rebuilt path.
  const confirmRename = (name: string) => {
    if (!pendingRename) return;

    const { path } = pendingRename;
    move.mutate({
      scope,
      paths: [path],
      destScope: scope,
      destination: () => parentDir(path) + name,
    });
  };

  return (
    <>
      <ConfirmDialog
        open={deleteDialog.open}
        onOpenChange={deleteDialog.setOpen}
        title={
          pendingDelete?.kind === "bulk"
            ? t(($) => $.documents.confirm.deleteDocuments, { count: pendingDelete.files.length })
            : pendingDelete?.kind === "directory"
              ? t(($) => $.documents.confirm.deleteFolder)
              : t(($) => $.documents.confirm.deleteDocument)
        }
        description={t(($) => $.documents.confirm.deleteDescription)}
        confirmLabel={t(($) => $.common.actions.delete)}
        onConfirm={confirmDelete}
      />

      <NameInputDialog
        open={renameDialog.open}
        onOpenChange={renameDialog.setOpen}
        title={
          pendingRename?.kind === "directory"
            ? t(($) => $.documents.confirm.renameFolder)
            : t(($) => $.documents.confirm.renameDocument)
        }
        description={t(($) => $.documents.confirm.renameDescription, {
          name: basename(pendingRename?.path ?? ""),
        })}
        label={t(($) => $.documents.confirm.name)}
        initialValue={basename(pendingRename?.path ?? "")}
        submitLabel={t(($) => $.common.actions.rename)}
        onSubmit={confirmRename}
      />
    </>
  );
}
