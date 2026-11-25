import {
  type DragTarget,
  type FeatureImplementation,
  type SelectionDataRef,
  type ItemInstance,
  dragAndDropFeature,
  expandAllFeature,
  hotkeysCoreFeature,
  keyboardDragAndDropFeature,
  renamingFeature,
  selectionFeature,
  syncDataLoaderFeature,
} from "@headless-tree/core";
import { useTree } from "@headless-tree/react/react-compiler";
import { type UseQueryResult, useMutation, useQueries } from "@tanstack/react-query";
import { type DragEvent, type MouseEvent, type SetStateAction, useState } from "react";

import {
  type DirectoryTreeResponse,
  PERSONAL_SCOPE,
  documentMutations,
  documentQueries,
  splitScopePath,
} from "@/api/documents";
import {
  TREE_ROOT,
  type EntryNode,
  type TreeNode,
  type Workspace,
  buildTreeIndex,
  dropFolder,
  entriesOf,
  filesOf,
  filterChildren,
  getNode,
  isWritableEntry,
  isWritableFolder,
  planMoves,
  planRename,
  uploadTarget,
} from "@/lib/document-tree";
import { below, followEach } from "@/lib/utils";
import { type Follow, useFollowedState, useWorkspaceFollow } from "@/hooks/use-followed-state";
import { useDialogTarget } from "@/hooks/use-dialog-target";
import { useFuzzySearch } from "@/hooks/use-fuzzy-search";
import { useSettingsStore } from "@/stores/settings-store";

declare module "@headless-tree/core" {
  interface TreeConfig<T> {
    /** Open *item*, on a plain click of its row or Enter on it. */
    onOpenItem?: (item: ItemInstance<T>) => void;
  }
}

/** Indent per tree level in pixels, an icon (16px) and its gap (8px), so a child's icon lines up under its folder's name. */
export const TREE_INDENT = 24;

/** The controls a row may hold, which act on their own and leave the row alone. */
const ROW_CONTROLS = "a, button, input, [role=checkbox]";

const isRowControl = (target: EventTarget | null) =>
  target instanceof Element && target.closest(ROW_CONTROLS) !== null;

interface RowDataRef extends SelectionDataRef {
  /** The selection a drag of an unselected entry replaced, restored once it ends. */
  selectionBeforeDrag?: string[];
}

/**
 * The selection is what the checkboxes show, so only they and modified clicks
 * change it: a plain click focuses the row and toggles a folder or opens a
 * file, Ctrl or Cmd toggles the row, and Shift extends from the last click.
 * Dragging an unselected entry moves it alone and keeps the selection, and a
 * click on a row's own control leaves the row alone.
 */
const rowControlsFeature: FeatureImplementation<TreeNode> = {
  key: "row-controls",
  overwrites: ["selection", "drag-and-drop"],
  itemInstance: {
    getProps: ({ tree, item, prev }) => ({
      ...prev?.(),
      onClick: (e: MouseEvent) => {
        if (isRowControl(e.target)) return;

        item.setFocused();

        if (e.shiftKey) {
          item.selectUpTo(e.ctrlKey || e.metaKey);

          return;
        }

        tree.getDataRef<RowDataRef>().current.selectUpToAnchorId = item.getId();

        if (e.ctrlKey || e.metaKey) item.toggleSelect();
        else if (!item.isFolder()) tree.getConfig().onOpenItem?.(item);
        else if (item.isExpanded()) item.collapse();
        else item.expand();
      },
    }),
    getDragHandleProps: ({ tree, item, prev }) => {
      const props = prev?.();
      const data = tree.getDataRef<RowDataRef>().current;

      return {
        ...props,
        onDragStart: (e: DragEvent) => {
          if (!item.isSelected()) data.selectionBeforeDrag = tree.getState().selectedItems;

          props?.onDragStart?.(e);
        },
        onDragEnd: (e: DragEvent) => {
          props?.onDragEnd?.(e);

          if (data.selectionBeforeDrag) tree.setSelectedItems(data.selectionBeforeDrag);

          data.selectionBeforeDrag = undefined;
        },
      };
    },
  },
};

const followOne = (path: string | null, follow: Follow) => (path === null ? null : follow(path));

const ignore = () => {};

function combineTrees(results: UseQueryResult<DirectoryTreeResponse>[]) {
  return {
    data: results.map((result) => result.data),
    errors: results.flatMap((result) => (result.error ? [result.error] : [])),
  };
}

/** The index of every workspace tree, and the files in it that a search matches against. */
function useTreeIndex(workspaces: readonly Workspace[]) {
  const trees = useQueries({
    queries: workspaces.map(({ scope }) => documentQueries.tree(scope)),
    combine: combineTrees,
  });
  const nodes = buildTreeIndex(
    workspaces.map((workspace, i) => ({ ...workspace, tree: trees.data[i] })),
  );
  const files = [...nodes.values()].flatMap((node) =>
    node.kind === "file"
      ? [{ id: node.id, name: node.name, path: splitScopePath(node.id).local }]
      : [],
  );

  return { nodes, files, errors: trees.errors };
}

interface DocumentTreeOptions {
  workspaces: readonly Workspace[];
  searchQuery: string;
  /** Upload dropped OS entries into the canonical folder *target*. */
  onUpload: (target: string, items: DataTransferItem[], files: File[]) => void;
}

/**
 * The one headless tree over every workspace, keyed by canonical path. Moves
 * are drops (by pointer or keyboard) into a folder, renames happen inline, OS
 * files dropped onto a folder upload into it, and the focused item decides
 * where uploads and new entries land. A search filters the tree to the
 * matching files and the folders leading to them, all expanded.
 *
 * Expansion persists in the settings store, while the selection, the focus,
 * and the open document live here, all of them following every workspace
 * change so a moved entry keeps its state and a deleted one drops out.
 */
export function useDocumentTree({ workspaces, searchQuery, onUpload }: DocumentTreeOptions) {
  const { nodes, files, errors } = useTreeIndex(workspaces);
  const { mutate: move } = useMutation(documentMutations.move());
  const found = useFuzzySearch(files, searchQuery, "name", "path");
  const expandedItems = useSettingsStore((s) => s.expandedItems);
  const setExpandedItems = useSettingsStore((s) => s.setExpandedItems);
  useWorkspaceFollow((follow) => setExpandedItems((items) => followEach(items, follow)));

  const [selectedItems, setSelectedItems] = useFollowedState<string[]>([], followEach);
  const [focusedItem, setFocusedItem] = useFollowedState<string | null>(PERSONAL_SCOPE, followOne);
  const [openPath, setOpenPath] = useFollowedState<string | null>(null, followOne);
  // The path outlives a close, so the dialog keeps its content while it animates out.
  const [documentOpen, setDocumentOpen] = useState(false);
  const deleteDialog = useDialogTarget<readonly EntryNode[]>([]);
  const similarityDialog = useDialogTarget("");

  const isSearching = searchQuery.trim().length > 0;
  // While searching, the children of the folders leading to a match, else none.
  const filtered = isSearching
    ? filterChildren(nodes, new Set(found.map((file) => file.id)))
    : null;
  const filteredFolders = filtered ? [...filtered.keys()] : [];
  const selectedEntries = entriesOf(nodes, selectedItems);
  const selectedFiles = filesOf(nodes, selectedEntries);
  const target = uploadTarget(nodes, focusedItem);

  // OS files land in the hovered folder, or a file's folder, and in the
  // upload target when dropped on the tree's empty space.
  const acceptsFiles = (dataTransfer: DataTransfer, { item }: DragTarget<TreeNode>) => {
    const node = item.getItemData();

    return dataTransfer.types.includes("Files") && (node.kind === "root" || node.writable);
  };

  // The tree only knows the ids of loaded workspaces, so a toggle keeps the
  // persisted ones of a workspace still loading or failing to.
  const awaitsWorkspace = (id: string) => {
    const workspace = nodes.get(splitScopePath(id).scope);

    return !nodes.has(id) && workspace?.kind === "workspace" && !workspace.loaded;
  };
  const setExpanded = (update: SetStateAction<string[]>) =>
    setExpandedItems((items) => [
      ...items.filter(awaitsWorkspace),
      ...(typeof update === "function" ? update(items.filter((id) => nodes.has(id))) : update),
    ]);

  const tree = useTree<TreeNode>({
    rootItemId: TREE_ROOT,
    getItemName: (item) => item.getItemData().name,
    isItemFolder: (item) => item.getItemData().kind !== "file",
    // Items resolve from the whole tree, so a selection hidden by a search
    // stays valid, while a search narrows the children.
    dataLoader: {
      getItem: (id) => getNode(nodes, id),
      getChildren: (id) => (filtered ? filtered.get(id) : nodes.get(id)?.children) ?? [],
    },
    indent: TREE_INDENT,
    // The backend keeps no order, so a drop always lands inside a folder.
    canReorder: false,
    // Kept ids may outlive their entries until the feed follows a change, or
    // while a workspace fails to load, and the tree throws on an unknown one.
    // The expanded items are new with every fresh tree, which rebuilds the item
    // list as the tree takes them, so no row of a vanished entry renders.
    state: {
      expandedItems: filtered ? filteredFolders : expandedItems.filter((id) => nodes.has(id)),
      selectedItems: selectedItems.filter((id) => nodes.has(id)),
      focusedItem: focusedItem !== null && nodes.has(focusedItem) ? focusedItem : null,
    },
    setExpandedItems: filtered ? ignore : setExpanded,
    setSelectedItems,
    setFocusedItem,
    canDrag: (items) => items.every((item) => isWritableEntry(item.getItemData())),
    canDrop: (items, { item }) =>
      isWritableFolder(item.getItemData()) &&
      items.every((dragged) => below(item.getId(), dragged.getId()) === null),
    canDragForeignDragObjectOver: acceptsFiles,
    canDropForeignDragObject: acceptsFiles,
    onDrop: (items, { item }) => {
      const change = planMoves(
        entriesOf(
          nodes,
          items.map((dragged) => dragged.getId()),
        ),
        item.getId(),
      );

      if (change.paths.length > 0) move(change);
    },
    onDropForeignDragObject: (dataTransfer, { item }) => {
      const node = item.getItemData();
      onUpload(
        node.kind === "root" ? target : dropFolder(node),
        [...dataTransfer.items],
        [...dataTransfer.files],
      );
    },
    canRename: (item) => isWritableEntry(item.getItemData()),
    onRename: (item, value) => {
      const node = item.getItemData();
      const name = value.trim();

      if (isWritableEntry(node) && name && name !== node.name) move(planRename(node, name));
    },
    onOpenItem: (item) => {
      if (item.getItemData().kind !== "file") return;

      setOpenPath(item.getId());
      setDocumentOpen(true);
    },
    hotkeys: {
      customOpenItem: {
        hotkey: "Enter",
        isEnabled: (tree) => !tree.getState().dnd && !tree.isRenamingItem(),
        handler: (e, tree) => {
          if (!isRowControl(e.target)) tree.getConfig().onOpenItem?.(tree.getFocusedItem());
        },
      },
    },
    features: [
      syncDataLoaderFeature,
      selectionFeature,
      hotkeysCoreFeature,
      dragAndDropFeature,
      keyboardDragAndDropFeature,
      renamingFeature,
      expandAllFeature,
      rowControlsFeature,
    ],
  });

  return {
    tree,
    nodes,
    errors,
    isSearching,
    selectedItems,
    /** The entries the selection stands for, and every file in or below them. */
    selectedEntries,
    selectedFiles,
    clearSelection: () => setSelectedItems([]),
    /** Canonical folder uploads and new entries land in. */
    target,
    resetTarget: () => setFocusedItem(PERSONAL_SCOPE),
    openPath,
    documentOpen,
    setDocumentOpen,
    /** The confirmation before deleting entries, from a row or the selection. */
    deleteDialog,
    /** The similarity of a folder case base, by its path. */
    similarityDialog,
  };
}

export type DocumentTree = ReturnType<typeof useDocumentTree>;
