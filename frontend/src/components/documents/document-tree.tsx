import { AssistiveDndState, type DndState, type HotkeysConfig } from "@headless-tree/core";
import { AssistiveTreeDescription } from "@headless-tree/react";
import { useMutation } from "@tanstack/react-query";
import { SearchXIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { documentMutations, useMutatingPaths } from "@/api/documents";
import { TREE_ROOT, type EntryNode, dropFolder, isCaseBaseFolder } from "@/lib/document-tree";
import { below } from "@/lib/utils";
import type { DocumentTree } from "@/hooks/use-document-tree";
import { useFileActions } from "@/hooks/use-file-actions";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { DocumentDialog } from "@/components/document-dialog";
import { ErrorAlert } from "@/components/common/error-alert";
import { StatusPanel } from "@/components/common/status-panel";
import { DeleteDialog } from "@/components/documents/delete-dialog";
import { SimilarityDialog } from "@/components/documents/similarity-dialog";
import { TreeRow } from "@/components/documents/tree-row";
import { VirtualList } from "@/components/common/virtual-list";

interface DocumentTreeViewProps {
  docTree: DocumentTree;
  /** The element scrolling the tree, whose rows mount only while in and near its view. */
  scrollElement: HTMLElement | null;
}

/**
 * Renders the document tree of {@link useDocumentTree} with the delete
 * confirmation and the dialog of the open document. While something is dragged over a folder, the folder and its
 * shown entries light up as the drop target.
 */
export function DocumentTreeView({ docTree, scrollElement }: DocumentTreeViewProps) {
  const { t } = useTranslation();
  const { tree, nodes, openPath, deleteDialog, similarityDialog } = docTree;
  const mutatingPaths = useMutatingPaths();
  const included = useDocumentFilterStore((s) => s.included);
  const excluded = useDocumentFilterStore((s) => s.excluded);
  const { mutateAsync: write } = useMutation(documentMutations.write());
  const actions = useFileActions();
  const showDelete = deleteDialog.show;
  const deleteEntry = (entry: EntryNode) => showDelete([entry]);
  const showSimilarity = similarityDialog.show;
  const openSimilarity = (entry: EntryNode) => showSimilarity(entry.id);

  const includedSet = new Set(included.map((entry) => entry.path));
  const excludedSet = new Set(excluded.map((entry) => entry.path));

  const describeMove = <T,>(
    dnd: DndState<T> | null | undefined,
    state: AssistiveDndState,
    hotkeys: HotkeysConfig<T>,
  ) => {
    const keys = {
      start: hotkeys.startDrag?.hotkey ?? "",
      up: hotkeys.dragUp?.hotkey ?? "",
      down: hotkeys.dragDown?.hotkey ?? "",
      drop: hotkeys.completeDrag?.hotkey ?? "",
      cancel: hotkeys.cancelDrag?.hotkey ?? "",
    };
    const moving = {
      items: dnd?.draggedItems?.map((item) => item.getItemName()).join(", ") ?? "",
      target: dnd?.dragTarget?.item.getItemName() ?? "",
    };

    switch (state) {
      case AssistiveDndState.Started:
        return t(($) => $.documents.tree.moveStarted, { ...moving, ...keys });
      case AssistiveDndState.Dragging:
        return t(($) => $.documents.tree.moveDragging, moving);
      case AssistiveDndState.Completed:
        return t(($) => $.documents.tree.moveCompleted, keys);
      case AssistiveDndState.Aborted:
        return t(($) => $.documents.tree.moveAborted, keys);
      default:
        return t(($) => $.documents.tree.moveHint, keys);
    }
  };

  const { dnd, renamingItem, renamingValue } = tree().getState();
  const dragTarget = dnd?.dragTarget;
  const dropId =
    dragTarget && dragTarget.item.getId() !== TREE_ROOT
      ? dropFolder(dragTarget.item.getItemData())
      : null;
  const dragged = new Set(dnd?.draggedItems?.map((item) => item.getId()));
  const items = tree().getItems();
  // The focused row stays mounted out of view, so it keeps the focus and the keyboard moves on from it.
  const focusedIndex = tree().getFocusedItem()?.getItemMeta().index;

  // The document the dialog is open on, resolved once for the write-action gates below.
  const openNode = openPath === null ? undefined : nodes.get(openPath);
  const openFile = openNode?.kind === "file" ? openNode : undefined;

  return (
    <div className="flex flex-col gap-1">
      {docTree.errors.map((error, i) => (
        <ErrorAlert key={i} error={error} />
      ))}
      {docTree.isSearching && items.length === 0 ? (
        <StatusPanel
          icon={SearchXIcon}
          title={t(($) => $.documents.manager.noMatches)}
          className="p-4"
        />
      ) : (
        <div
          {...tree().getContainerProps(t(($) => $.documents.canvas.documents))}
          className="min-h-16 outline-none"
        >
          <AssistiveTreeDescription tree={tree()} getLabel={describeMove} />
          <VirtualList
            items={items}
            getKey={(item) => item.getId()}
            scrollElement={scrollElement}
            estimateSize={36}
            gap={2}
            pinned={focusedIndex}
          >
            {(item) => {
              const id = item.getId();
              const node = item.getItemData();
              const { level, posInSet, setSize } = item.getItemMeta();

              return (
                <TreeRow
                  item={item}
                  node={node}
                  level={level}
                  posInSet={posInSet}
                  setSize={setSize}
                  selected={item.isSelected()}
                  expanded={item.isExpanded()}
                  focused={item.isFocused()}
                  renameValue={renamingItem === id ? (renamingValue ?? "") : null}
                  filterState={
                    includedSet.has(id) ? "included" : excludedSet.has(id) ? "excluded" : undefined
                  }
                  isDropTarget={dropId !== null && below(id, dropId) !== null}
                  isDragged={dragged.has(id)}
                  isMutating={mutatingPaths.has(id)}
                  onDelete={deleteEntry}
                  onSimilarity={isCaseBaseFolder(node) ? openSimilarity : undefined}
                />
              );
            }}
          </VirtualList>
        </div>
      )}

      <SimilarityDialog
        open={similarityDialog.open}
        onOpenChange={similarityDialog.setOpen}
        path={similarityDialog.target}
      />

      <DeleteDialog
        open={deleteDialog.open}
        onOpenChange={deleteDialog.setOpen}
        entries={deleteDialog.target}
      />

      <DocumentDialog
        open={docTree.documentOpen && openPath !== null}
        onOpenChange={docTree.setDocumentOpen}
        filename={openPath ?? ""}
        // Every workspace document opens in managed mode (metadata + markdown
        // download), only the write actions below are gated on the workspace.
        showMetadata
        editable={openFile?.writable ?? false}
        onSave={(filename, content) => write({ paths: [filename], content })}
        onRechunk={openFile?.writable ? () => actions.rechunk([openFile]) : undefined}
        rechunking={openPath !== null && mutatingPaths.has(openPath)}
        onReconvert={
          openFile?.writable && openFile.entry.has_original
            ? () => actions.reconvert([openFile])
            : undefined
        }
        onDownloadOriginal={
          openFile?.entry.has_original ? () => actions.download([openFile]) : undefined
        }
      />
    </div>
  );
}
