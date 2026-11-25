import type { ItemInstance, ItemMeta } from "@headless-tree/core";
import { FileText, Folder, FolderOpen, type LucideIcon, Pencil, Scale, Trash2 } from "lucide-react";
import { memo } from "react";
import { useTranslation } from "react-i18next";

import { type EntryNode, type TreeNode, isWritableEntry } from "@/lib/document-tree";
import { cn } from "@/lib/utils";
import { TREE_INDENT } from "@/hooks/use-document-tree";
import { useDocumentFilterStore } from "@/stores/document-filter-store";
import { Spinner } from "@/components/common/spinner";
import { TooltipButton } from "@/components/common/tooltip-button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  FilterToggleButtons,
  type FilterEntryState,
} from "@/components/documents/filter-toggle-buttons";
import { ChunkCountBadge, REVEAL_ON_HOVER } from "@/components/row-buttons";

/** A hover-revealed icon action (rename, delete), identical across file and directory rows. */
function RowAction({
  icon: Icon,
  label,
  onClick,
  disabled,
  destructive,
}: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <TooltipButton
      size="icon-xs"
      label={label}
      destructive={destructive}
      className={REVEAL_ON_HOVER}
      onClick={onClick}
      disabled={disabled}
    >
      <Icon />
    </TooltipButton>
  );
}

/** A file's icon, or a folder's showing whether it is open, which doubles as its toggle cue. */
function RowIcon({ node, expanded }: { node: TreeNode; expanded: boolean }) {
  const className = "size-4 shrink-0 text-muted-foreground";

  if (node.kind === "file") return <FileText className={className} />;

  return expanded ? <FolderOpen className={className} /> : <Folder className={className} />;
}

/**
 * What a row shows, passed as values so an unchanged row skips rendering while
 * the tree state changes elsewhere, a drag hovering other rows included.
 */
interface TreeRowProps {
  item: ItemInstance<TreeNode>;
  node: TreeNode;
  meta: ItemMeta;
  selected: boolean;
  expanded: boolean;
  focused: boolean;
  /** The edited name while the row is renamed, else null. */
  renameValue: string | null;
  filterState: FilterEntryState;
  isDropTarget: boolean;
  isDragged: boolean;
  isMutating: boolean;
  onDelete: (entry: EntryNode) => void;
  /** Opens the similarity of a folder case base, undefined for any other row. */
  onSimilarity?: (entry: EntryNode) => void;
}

/**
 * One tree item: the selection checkbox, the indented name cluster, its
 * actions, then metadata. A fixed min-height keeps the row from growing when
 * its hover buttons appear.
 */
export const TreeRow = memo(function TreeRow({
  item,
  node,
  meta,
  selected,
  expanded,
  focused,
  renameValue,
  filterState,
  isDropTarget,
  isDragged,
  isMutating,
  onDelete,
  onSimilarity,
}: TreeRowProps) {
  // The item is a mutable instance whose reads the React Compiler would cache,
  // so the row renders uncompiled and `memo` keys it on the values passed in.
  "use no memo";
  const { t } = useTranslation();
  const toggleInclude = useDocumentFilterStore((s) => s.toggleInclude);
  const toggleExclude = useDocumentFilterStore((s) => s.toggleExclude);

  if (node.kind === "root") return null;

  // A workspace filters as a directory, its bare scope.
  const filterEntry = { path: item.getId(), directory: node.kind !== "file" };

  return (
    <div
      {...item.getProps()}
      className={cn(
        "group/row flex min-h-9 items-center gap-2 rounded-xl px-2 outline-none hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring",
        focused && "bg-muted/50",
        selected && "bg-accent hover:bg-accent",
        isDropTarget && "bg-primary/10 ring-1 ring-primary ring-inset",
        isDragged && "opacity-50",
      )}
    >
      <Checkbox
        checked={selected}
        onCheckedChange={() => item.toggleSelect()}
        tabIndex={-1}
        aria-label={node.name}
      />
      <div
        className="flex min-w-0 flex-1 items-center gap-2"
        style={{ paddingLeft: `${meta.level * TREE_INDENT}px` }}
      >
        <RowIcon node={node} expanded={expanded} />
        {renameValue === null ? (
          <span className={cn("truncate text-sm", node.kind !== "file" && "font-medium")}>
            {node.name}
          </span>
        ) : (
          <Input
            {...item.getRenameInputProps()}
            aria-label={t(($) => $.common.actions.rename)}
            className="h-7"
          />
        )}
        {(isMutating || (node.kind === "workspace" && !node.loaded)) && (
          <Spinner
            aria-label={t(($) => $.common.states.working)}
            className="shrink-0 text-muted-foreground"
          />
        )}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <FilterToggleButtons
          state={filterState}
          onInclude={() => toggleInclude(filterEntry)}
          onExclude={() => toggleExclude(filterEntry)}
          revealOnHover
        />
        {onSimilarity && node.kind === "directory" && (
          <RowAction
            icon={Scale}
            label={t(($) => $.documents.similarity.tab)}
            onClick={() => onSimilarity(node)}
          />
        )}
        {isWritableEntry(node) && (
          <>
            <RowAction
              icon={Pencil}
              label={t(($) => $.common.actions.rename)}
              onClick={() => item.startRenaming()}
              disabled={isMutating}
            />
            <RowAction
              icon={Trash2}
              label={
                node.kind === "directory"
                  ? t(($) => $.documents.tree.deleteFolder)
                  : t(($) => $.common.actions.delete)
              }
              onClick={() => onDelete(node)}
              disabled={isMutating}
              destructive
            />
          </>
        )}
      </div>
      {node.kind === "file"
        ? node.entry.chunk_count != null && <ChunkCountBadge count={node.entry.chunk_count} />
        : (node.kind === "workspace" || node.fileCount > 0) && (
            <Badge variant="secondary">{node.fileCount}</Badge>
          )}
    </div>
  );
});
