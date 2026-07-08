import { combine } from "@atlaskit/pragmatic-drag-and-drop/combine";
import { draggable, dropTargetForElements } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { dropTargetForExternal } from "@atlaskit/pragmatic-drag-and-drop/external/adapter";
import { containsFiles, getFiles } from "@atlaskit/pragmatic-drag-and-drop/external/file";

/**
 * Native drag-and-drop for the document tree. One model covers both gestures a
 * native file manager offers: dragging a row onto a folder (an internal move)
 * and dropping OS files onto a folder (an upload into it). Rows render flat as
 * siblings — never DOM-nested — so every drop target stands alone and no
 * innermost-target arbitration is needed.
 */

const TREE_ITEM_KEY = "hivegent-tree-item";

/** The payload a dragged file/directory row carries. */
export interface TreeItemDrag {
  /** Source workspace scope (`~` or `@<group>`). */
  scope: string;
  kind: "file" | "directory";
  /** The dragged local paths: one row, or the whole selection when it moves. */
  paths: string[];
}

/** Recover a tree drag payload from adapter data, or `null` for other drags. */
export function getTreeItemDrag(data: Record<string | symbol, unknown>): TreeItemDrag | null {
  return (data[TREE_ITEM_KEY] as TreeItemDrag | undefined) ?? null;
}

/** Visual cue a drop target shows while a drag hovers it. */
export type TreeDropState = "none" | "active" | "blocked";

/**
 * Row background/ring for each drop state: an accepted drag (a valid move or a
 * file upload) invites the drop, a blocked one (cross-workspace or no-op)
 * refuses it. Shared by the tree rows and the scope-root header target.
 */
export const DROP_CLASSES: Record<TreeDropState, string> = {
  none: "",
  active: "ring-1 ring-inset ring-primary bg-primary/10",
  blocked: "ring-1 ring-inset ring-destructive bg-destructive/10 cursor-no-drop",
};

const parentDir = (path: string): string =>
  path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";

/**
 * Whether moving `drag` into `destDir` (a local dir, `""` for the scope root)
 * would actually change anything: same workspace only, never a directory into
 * itself or a descendant, and never a no-op onto a row's current parent.
 */
export function isValidMove(drag: TreeItemDrag, destScope: string, destDir: string): boolean {
  if (drag.scope !== destScope) return false;

  if (drag.kind === "directory") {
    const src = drag.paths[0];
    if (destDir === src || destDir.startsWith(`${src}/`)) return false;
    return parentDir(src) !== destDir;
  }

  return drag.paths.some((p) => parentDir(p) !== destDir);
}

/** Destination for a drop target: the local dir it represents, `""` = root. */
interface TreeDropConfig {
  scope: string;
  destDir: string;
  onMove: (drag: TreeItemDrag) => void;
  onUpload: (items: DataTransferItem[], files: File[]) => void;
}

interface TreeRowConfig {
  element: HTMLElement;
  /**
   * Resolves this row's drag payload at drag-start, or `null` for a
   * drop-only row. Lazy so a selection-aware drag reads the live selection
   * without re-registering the row every time it changes.
   */
  drag: (() => TreeItemDrag) | null;
  /** Drop destination, or `null` for a row that is only a drag source. */
  drop: TreeDropConfig | null;
  onDragging?: (dragging: boolean) => void;
  onDropState?: (state: TreeDropState) => void;
}

/**
 * Wire one tree row (or the scope-root target) as a drag source and/or drop
 * target, returning a combined teardown. A cross-workspace or no-op element
 * drag is still accepted so the row can flash a "blocked" cue, but its drop
 * no-ops — only a valid move runs `onMove`.
 */
export function registerTreeRow({
  element,
  drag,
  drop,
  onDragging,
  onDropState,
}: TreeRowConfig): () => void {
  const cleanups: (() => void)[] = [];

  if (drag) {
    cleanups.push(
      draggable({
        element,
        getInitialData: () => ({ [TREE_ITEM_KEY]: drag() }),
        onDragStart: () => onDragging?.(true),
        onDrop: () => onDragging?.(false),
      }),
    );
  }

  if (drop) {
    const { scope, destDir, onMove, onUpload } = drop;
    cleanups.push(
      dropTargetForElements({
        element,
        canDrop: ({ source }) => getTreeItemDrag(source.data) != null,
        onDragEnter: ({ source }) => {
          const item = getTreeItemDrag(source.data);
          onDropState?.(item && isValidMove(item, scope, destDir) ? "active" : "blocked");
        },
        onDragLeave: () => onDropState?.("none"),
        onDrop: ({ source }) => {
          onDropState?.("none");
          const item = getTreeItemDrag(source.data);
          if (item && isValidMove(item, scope, destDir)) onMove(item);
        },
      }),
      dropTargetForExternal({
        element,
        canDrop: ({ source }) => containsFiles({ source }),
        onDragEnter: () => onDropState?.("active"),
        onDragLeave: () => onDropState?.("none"),
        onDrop: ({ source }) => {
          onDropState?.("none");
          onUpload(source.items, getFiles({ source }));
        },
      }),
    );
  }

  return combine(...cleanups);
}
