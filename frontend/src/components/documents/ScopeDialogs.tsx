import { forwardRef, useCallback, useImperativeHandle, useState } from "react";

import { commonParentDir } from "@/lib/utils";
import { useDocumentsStore } from "../../stores/documents-store";
import { CreateDirectoryDialog } from "../CreateDirectoryDialog";
import { MoveDocumentDialog } from "../MoveDocumentDialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../ui/alert-dialog";

type DeleteTarget =
  | { kind: "file"; path: string }
  | { kind: "directory"; path: string }
  | { kind: "bulk"; files: string[] };

/** Imperative openers exposed to the owning ScopeSection. */
export interface ScopeDialogsHandle {
  moveFile: (path: string) => void;
  moveDir: (path: string) => void;
  bulkMove: (files: string[]) => void;
  createSubdir: (parent: string) => void;
  deleteFile: (path: string) => void;
  deleteDir: (path: string) => void;
  bulkDelete: (files: string[]) => void;
}

interface ScopeDialogsProps {
  /** Workspace scope: `~` for personal, `@<group>` for a group. */
  scope: string;
  /** Run after a bulk move/delete starts (e.g. to clear the selection). */
  onBulkDone: () => void;
}

/**
 * The move / delete / create-directory dialogs for one scope. Owns their state
 * and runs the matching store mutations, so ScopeSection only has to call the
 * imperative openers from its tree and bulk actions.
 */
export const ScopeDialogs = forwardRef<ScopeDialogsHandle, ScopeDialogsProps>(function ScopeDialogs(
  { scope, onBulkDone },
  ref,
) {
  const storeMove = useDocumentsStore((s) => s.move);
  const storeBulkMove = useDocumentsStore((s) => s.bulkMove);
  const storeMoveDir = useDocumentsStore((s) => s.moveDir);
  const createDir = useDocumentsStore((s) => s.createDir);
  const deleteDir = useDocumentsStore((s) => s.deleteDir);
  const removeDoc = useDocumentsStore((s) => s.remove);
  const storeBulkDelete = useDocumentsStore((s) => s.bulkDelete);

  const [moveFilePath, setMoveFilePath] = useState<string | null>(null);
  const [moveDirPath, setMoveDirPath] = useState<string | null>(null);
  const [bulkMoveFiles, setBulkMoveFiles] = useState<string[] | null>(null);
  const [createDirParent, setCreateDirParent] = useState<string | undefined>(undefined);
  const [showCreateDir, setShowCreateDir] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<DeleteTarget | null>(null);

  useImperativeHandle(
    ref,
    () => ({
      moveFile: setMoveFilePath,
      moveDir: setMoveDirPath,
      bulkMove: setBulkMoveFiles,
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

  const handleBulkMove = useCallback(
    async (destinationDir: string) => {
      const files = bulkMoveFiles ?? [];
      setBulkMoveFiles(null);
      onBulkDone();
      // Preserve the selection's directory structure: each file keeps its
      // path relative to the selection's common parent directory.  The bulk
      // endpoint also prunes source directories the move leaves empty.
      const commonParent = commonParentDir(files);
      const moves = files
        .map((source) => {
          const relative = source.slice(commonParent.length);
          return {
            source,
            destination: destinationDir ? `${destinationDir}/${relative}` : relative,
          };
        })
        .filter(({ source, destination }) => destination !== source);
      if (moves.length > 0) await storeBulkMove(scope, moves);
    },
    [bulkMoveFiles, onBulkDone, storeBulkMove, scope],
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
      <MoveDocumentDialog
        open={moveFilePath !== null}
        onOpenChange={(open) => !open && setMoveFilePath(null)}
        currentPath={moveFilePath ?? ""}
        onMove={(destination) => {
          if (moveFilePath) void storeMove(scope, moveFilePath, destination);
        }}
      />
      <MoveDocumentDialog
        open={moveDirPath !== null}
        onOpenChange={(open) => !open && setMoveDirPath(null)}
        currentPath={moveDirPath ?? ""}
        isDirectory
        onMove={(destination) => {
          if (moveDirPath) void storeMoveDir(scope, moveDirPath, destination);
        }}
      />
      <MoveDocumentDialog
        open={bulkMoveFiles !== null}
        onOpenChange={(open) => !open && setBulkMoveFiles(null)}
        bulkFileCount={bulkMoveFiles?.length ?? 0}
        onMove={(dir) => void handleBulkMove(dir)}
      />
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
