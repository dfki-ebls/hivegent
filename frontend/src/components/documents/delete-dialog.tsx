import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { documentMutations } from "@/api/documents";
import type { EntryNode } from "@/lib/document-tree";
import { ConfirmDialog } from "@/components/confirm-dialog";

interface DeleteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The entries to delete, kept while the dialog animates out. */
  entries: readonly EntryNode[];
}

/**
 * Asks before deleting tree entries, then deletes them in one request that
 * names each directory, so a stale view cannot delete a file as a folder.
 * The selection follows the delete, so it needs no clearing.
 */
export function DeleteDialog({ open, onOpenChange, entries }: DeleteDialogProps) {
  const { t } = useTranslation();
  const { mutate: remove } = useMutation(documentMutations.remove());
  const single = entries.length === 1 ? entries[0] : undefined;

  const confirm = () => {
    onOpenChange(false);
    remove({
      paths: entries.map((entry) => entry.id),
      dirs: new Set(entries.flatMap((entry) => (entry.kind === "directory" ? [entry.id] : []))),
    });
  };

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={
        single?.kind === "directory"
          ? t(($) => $.documents.confirm.deleteFolder)
          : single
            ? t(($) => $.documents.confirm.deleteDocument)
            : t(($) => $.documents.confirm.deleteEntries, { count: entries.length })
      }
      description={t(($) => $.documents.confirm.deleteDescription)}
      confirmLabel={t(($) => $.common.actions.delete)}
      onConfirm={confirm}
    />
  );
}
