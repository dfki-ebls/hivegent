import { useMutation } from "@tanstack/react-query";
import { ChevronsDownUp, ChevronsUpDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { canonicalPath, formatTarget, documentMutations } from "@/api/documents";
import { errorMessage } from "@/lib/utils";
import {
  buildCollectionZip,
  buildCollectionZipFromDirectoryInput,
  classifyDropItems,
  collectionName,
  directoryInputName,
} from "@/lib/collection-upload";
import { useDocumentTree } from "@/hooks/use-document-tree";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { selectHasPendingUploads, useUploadQueue } from "@/stores/upload-queue-store";
import { DocumentDialog } from "@/components/document-dialog";
import { TooltipButton } from "@/components/common/tooltip-button";
import { SearchInput } from "@/components/search-input";
import { NameInputDialog } from "@/components/documents/name-input-dialog";
import { BulkActionBar } from "@/components/documents/bulk-action-bar";
import { DocumentTreeView } from "@/components/documents/document-tree";
import { UploadArea } from "@/components/documents/upload-area";

export function DocumentManager() {
  const { t } = useTranslation();
  const { mutateAsync: write } = useMutation(documentMutations.write());
  const createDir = useMutation(documentMutations.createDir());
  const enqueueFiles = useUploadQueue((s) => s.enqueueFiles);
  const enqueueCollection = useUploadQueue((s) => s.enqueueCollection);
  const reportUpload = useUploadQueue((s) => s.report);
  const hasPendingUploads = useUploadQueue(selectHasPendingUploads);

  const workspaces = useWorkspaces();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const directoryInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [newDocOpen, setNewDocOpen] = useState(false);
  const [showCreateDir, setShowCreateDir] = useState(false);

  // Warn before leaving only while bytes are still local: once an upload hands
  // off to a job it is server-side work that survives a reload (the feed
  // re-seeds it).
  useEffect(() => {
    if (!hasPendingUploads) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasPendingUploads]);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };
  // Moving onto a child of the drop zone also leaves the zone itself, which must not end the drag.
  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();

    if (!(e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget))) {
      setIsDragging(false);
    }
  };

  // Classify dropped OS entries and enqueue them into `target` (a canonical
  // directory). Shared by the top drop zone (which uses the upload target) and
  // drops onto the tree (which pass the folder dropped on).
  const uploadTo = async (target: string, items: DataTransferItem[], files: File[]) => {
    if (files.length === 0 && items.length === 0) return;
    try {
      const classification = await classifyDropItems(items, files);
      const hasCollection =
        classification.directories.length > 0 || classification.zipFiles.length > 0;
      if (hasCollection) {
        const name = collectionName(classification);
        enqueueCollection(target, name, (signal) =>
          buildCollectionZip(classification, name, signal),
        );
      } else {
        enqueueFiles(target, classification.looseFiles);
      }
    } catch (err) {
      // The drop fails before any queue item exists, so surface it as its own
      // failed tray row (a collection that fails mid-build already shows as one).
      reportUpload(
        target,
        t(($) => $.documents.manager.droppedItems),
        errorMessage(err),
      );
    }
  };

  const docTree = useDocumentTree({ workspaces, searchQuery, onUpload: uploadTo });
  // Every upload and new entry lands in the folder focused in the tree.
  const targetDir = docTree.target;

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    void uploadTo(targetDir, Array.from(e.dataTransfer.items), Array.from(e.dataTransfer.files));
  };

  // Picked files run through the same classifier as drops, so a selected ZIP is
  // extracted into a collection instead of stored as one opaque document. The
  // picker yields no directory entries, so only loose files and archives arrive.
  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const captured = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = "";
    void uploadTo(targetDir, [], captured);
  };

  const handleDirectoryInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    // Snapshot the files before resetting the input clears its FileList; the
    // archive then builds inside the queue from the held File objects.
    const captured = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = "";
    if (captured.length > 0) {
      const name = directoryInputName(captured);
      enqueueCollection(targetDir, name, () =>
        buildCollectionZipFromDirectoryInput(captured, name),
      );
    }
  };

  // New in-browser markdown is written synchronously (not as a background job)
  // in "create" mode, so a name collision rejects with a 409 that propagates to
  // the dialog (which stays open with the content) instead of silently
  // overwriting the existing document; the entry appears once the tree refreshes.
  const handleSaveNew = async (filename: string, content: string) => {
    // New documents are markdown: the backend always chunks the content as the
    // `<stem>.md` description, so the on-disk name must carry the `.md` suffix
    // or the entry and its chunk count drift apart.
    const markdownName = filename.replace(/\.md$/i, "") + ".md";
    await write({
      paths: [canonicalPath(targetDir, markdownName)],
      content,
      mode: "create",
    });
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* One scroll container with a header that sticks on tall enough windows:
          the upload area, pipeline bar, and search stay pinned while the
          document tree scrolls. Keeping the header inside the scroll container
          (rather than a separate fixed box) means the wheel scrolls the list
          even while the pointer is over the search box. On short windows the
          header scrolls away with the list to reclaim vertical space. */}
      <div className="top-0 z-10 bg-background tall:sticky">
        <UploadArea
          isDragging={isDragging}
          fileInputRef={fileInputRef}
          directoryInputRef={directoryInputRef}
          target={targetDir}
          onResetTarget={docTree.resetTarget}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onFileInputChange={handleFileInputChange}
          onDirectoryInputChange={handleDirectoryInputChange}
          onSelectFiles={() => fileInputRef.current?.click()}
          onSelectDirectory={() => directoryInputRef.current?.click()}
          onNewDocument={() => setNewDocOpen(true)}
          onNewFolder={() => setShowCreateDir(true)}
        />

        {/* A selection swaps its actions in for the search, one row of the
            same height, so the tree below never shifts. */}
        <div className="flex items-center gap-1 p-4 pb-2">
          {docTree.selectedItems.length > 0 ? (
            <BulkActionBar docTree={docTree} />
          ) : (
            <>
              <SearchInput
                value={searchQuery}
                onChange={setSearchQuery}
                placeholder={t(($) => $.documents.manager.searchPlaceholder)}
              />
              <TooltipButton
                variant="ghost"
                label={t(($) => $.documents.tree.expandAll)}
                onClick={() => void docTree.tree().expandAll()}
                disabled={docTree.isSearching}
              >
                <ChevronsUpDown />
              </TooltipButton>
              <TooltipButton
                variant="ghost"
                label={t(($) => $.documents.tree.collapseAll)}
                onClick={() => docTree.tree().collapseAll()}
                disabled={docTree.isSearching}
              >
                <ChevronsDownUp />
              </TooltipButton>
            </>
          )}
        </div>
      </div>

      <div className="px-4 pb-4">
        <DocumentTreeView docTree={docTree} />
      </div>

      <DocumentDialog
        open={newDocOpen}
        onOpenChange={setNewDocOpen}
        filename="new-document.md"
        editable
        isNew
        target={targetDir}
        onSave={handleSaveNew}
      />

      <NameInputDialog
        open={showCreateDir}
        onOpenChange={setShowCreateDir}
        title={t(($) => $.documents.confirm.createFolder)}
        description={t(($) => $.documents.confirm.createFolderDescription, {
          target: formatTarget(targetDir, workspaces),
        })}
        label={t(($) => $.documents.confirm.folderName)}
        placeholder={t(($) => $.documents.confirm.folderPlaceholder)}
        submitLabel={t(($) => $.common.actions.create)}
        onSubmit={(name) => {
          createDir.mutate({ paths: [canonicalPath(targetDir, name)] });
        }}
      />
    </div>
  );
}
