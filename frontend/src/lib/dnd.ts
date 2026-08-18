import { autoScrollForElements } from "@atlaskit/pragmatic-drag-and-drop-auto-scroll/element";
import { autoScrollForExternal } from "@atlaskit/pragmatic-drag-and-drop-auto-scroll/external";
import {
  draggable,
  dropTargetForElements,
} from "@atlaskit/pragmatic-drag-and-drop/adapter/element-adapter";
import { dropTargetForExternal } from "@atlaskit/pragmatic-drag-and-drop/adapter/drop-target-for-external";
import { combine } from "@atlaskit/pragmatic-drag-and-drop/utils/combine";
import { containsFiles } from "@atlaskit/pragmatic-drag-and-drop/utils/contains-files";
import { getFiles } from "@atlaskit/pragmatic-drag-and-drop/utils/get-files";

import { parentDir } from "@/lib/utils";

/**
 * Native drag-and-drop for the document tree. One model covers both gestures a
 * native file manager offers: dragging a row onto a directory (an internal
 * move) and dropping OS files onto one (an upload into it). Every row is a drop
 * target for the directory it belongs to, files included. Rows render flat as
 * siblings — never DOM-nested — so every target stands alone and no
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
 * file upload) invites the drop, a blocked one (a no-op or a directory dropped
 * into itself) refuses it. Shared by the tree rows and the scope-root header
 * target.
 */
export const DROP_CLASSES: Record<TreeDropState, string> = {
  none: "",
  active: "ring-1 ring-inset ring-primary bg-primary/10",
  blocked: "ring-1 ring-inset ring-destructive bg-destructive/10 cursor-no-drop",
};

/**
 * Whether moving `drag` into `destDir` (a local dir, `""` for the scope root)
 * would actually change anything. A move into a different workspace always
 * re-homes the entry, so it is always valid (both ends are write-gated in the
 * UI and re-checked by the backend); the no-op and into-itself guards only
 * apply when the source and destination share a scope.
 */
export function isValidMove(drag: TreeItemDrag, destScope: string, destDir: string): boolean {
  if (drag.scope !== destScope) return true;

  // `parentDir` yields the `dir/`-style prefix form, so compare against the
  // destination in that same form.
  const destPrefix = destDir ? `${destDir}/` : "";

  if (drag.kind === "directory") {
    const src = drag.paths[0];
    if (destDir === src || destDir.startsWith(`${src}/`)) return false;
    return parentDir(src) !== destPrefix;
  }

  return drag.paths.some((p) => parentDir(p) !== destPrefix);
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
 * target, returning a combined teardown. A no-op element drag (or a directory
 * dropped into itself) is still accepted so the row can flash a "blocked" cue,
 * but its drop no-ops — only a valid move runs `onMove`.
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

/**
 * Scroll `element` while a drag hovers near its top or bottom edge, so an entry
 * can be dragged into a workspace that is off-screen in one gesture instead of
 * dropping it halfway and scrolling by hand.
 */
export function registerTreeAutoScroll(element: HTMLElement): () => void {
  const config = { element, getAllowedAxis: () => "vertical" as const };
  return combine(autoScrollForElements(config), autoScrollForExternal(config));
}
