import { forwardRef, useCallback, useImperativeHandle, useState } from "react";

import { useDocumentsStore } from "@/stores/documents-store";
import { CreateDirectoryDialog } from "@/components/CreateDirectoryDialog";
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

/** Imperative openers exposed to the owning ScopeSection. */
export interface ScopeDialogsHandle {
  createSubdir: (parent: string) => void;
  deleteFile: (path: string) => void;
  deleteDir: (path: string) => void;
  bulkDelete: (files: string[]) => void;
}

interface ScopeDialogsProps {
  /** Workspace scope: `~` for personal, `@<group>` for a group. */
  scope: string;
  /** Run after a bulk delete starts (e.g. to clear the selection). */
  onBulkDone: () => void;
}

/**
 * The create-directory and delete confirmation dialogs for one scope. Owns their
 * state and runs the matching store mutations, so ScopeSection only has to call
 * the imperative openers from its tree and bulk actions. Moves happen through
 * native drag-and-drop, not a dialog.
 */
export const ScopeDialogs = forwardRef<ScopeDialogsHandle, ScopeDialogsProps>(function ScopeDialogs(
  { scope, onBulkDone },
  ref,
) {
  const createDir = useDocumentsStore((s) => s.createDir);
  const deleteDir = useDocumentsStore((s) => s.deleteDir);
  const removeDoc = useDocumentsStore((s) => s.remove);
  const storeBulkDelete = useDocumentsStore((s) => s.bulkDelete);

  const [createDirParent, setCreateDirParent] = useState<string | undefined>(undefined);
  const [showCreateDir, setShowCreateDir] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<DeleteTarget | null>(null);

  useImperativeHandle(
    ref,
    () => ({
      createSubdir: (parent) => {
        setCreateDirParent(parent || undefined);
        setShowCreateDir(true);
      },
      deleteFile: (path) => setPendingDelete({ kind: "file", path }),
      deleteDir: (path) => setPendingDelete({ kind: "directory", path }),
      bulkDelete: (files) => setPendingDelete({ kind: "bulk", files }),
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

  return (
    <>
      <CreateDirectoryDialog
        open={showCreateDir}
        onOpenChange={setShowCreateDir}
        parentPath={createDirParent}
        onCreate={(path) => createDir(scope, path)}
      />
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
    </>
  );
});
