import { forwardRef, useCallback, useImperativeHandle, useState } from "react";

import { useDocumentsStore } from "@/stores/documents-store";
import { basename, parentDir } from "@/lib/utils";
import { NameInputDialog } from "@/components/documents/NameInputDialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

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
  /** Workspace scope: `~` for personal, `@<group>` for a group. */
  scope: string;
  /** Run after a bulk delete starts (e.g. to clear the selection). */
  onBulkDone: () => void;
}

/**
 * The delete confirmation and rename dialogs for one scope. Owns their state and
 * runs the matching store mutations, so ScopeSection only has to call the
 * imperative openers from its tree and bulk actions. A rename is a same-directory
 * move, so it reuses the store's move actions. Creating directories happens
 * through the document manager's toolbar; cross-directory moves happen through
 * native drag-and-drop.
 */
export const ScopeDialogs = forwardRef<ScopeDialogsHandle, ScopeDialogsProps>(function ScopeDialogs(
  { scope, onBulkDone },
  ref,
) {
  const deleteDir = useDocumentsStore((s) => s.deleteDir);
  const removeDoc = useDocumentsStore((s) => s.remove);
  const storeBulkDelete = useDocumentsStore((s) => s.bulkDelete);
  const move = useDocumentsStore((s) => s.move);
  const moveDir = useDocumentsStore((s) => s.moveDir);

  const [pendingDelete, setPendingDelete] = useState<DeleteTarget | null>(null);
  const [pendingRename, setPendingRename] = useState<RenameTarget | null>(null);

  useImperativeHandle(
    ref,
    () => ({
      deleteFile: (path) => setPendingDelete({ kind: "file", path }),
      deleteDir: (path) => setPendingDelete({ kind: "directory", path }),
      bulkDelete: (files) => setPendingDelete({ kind: "bulk", files }),
      renameFile: (path) => setPendingRename({ kind: "file", path }),
      renameDir: (path) => setPendingRename({ kind: "directory", path }),
    }),
    [],
  );

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setPendingDelete(null);
    switch (pendingDelete.kind) {
      case "file":
        await removeDoc(scope, pendingDelete.path);
        break;
      case "directory":
        await deleteDir(scope, pendingDelete.path);
        break;
      case "bulk":
        onBulkDone();
        await storeBulkDelete(scope, pendingDelete.files);
        break;
    }
  }, [pendingDelete, removeDoc, deleteDir, storeBulkDelete, onBulkDone, scope]);

  // A rename keeps the entry's parent directory and swaps its basename, which is
  // exactly a same-scope move to the rebuilt path.
  const confirmRename = useCallback(
    (name: string) => {
      if (!pendingRename) return;
      const { kind, path } = pendingRename;
      setPendingRename(null);
      const destination = parentDir(path) + name;

      if (kind === "file") {
        void move(scope, path, scope, destination);
      } else {
        void moveDir(scope, path, scope, destination);
      }
    },
    [pendingRename, move, moveDir, scope],
  );

  return (
    <>
      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingDelete?.kind === "bulk"
                ? `Delete ${pendingDelete.files.length} documents?`
                : pendingDelete?.kind === "directory"
                  ? "Delete directory?"
                  : "Delete document?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              This action permanently deletes the selected content and its chunks. It cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => void confirmDelete()}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <NameInputDialog
        open={pendingRename !== null}
        onOpenChange={(open) => !open && setPendingRename(null)}
        title={`Rename ${pendingRename?.kind === "directory" ? "folder" : "document"}`}
        description={
          pendingRename ? `Enter a new name for ${basename(pendingRename.path)}.` : ""
        }
        label="Name"
        initialValue={pendingRename ? basename(pendingRename.path) : ""}
        submitLabel="Rename"
        onSubmit={confirmRename}
      />
    </>
  );
});
