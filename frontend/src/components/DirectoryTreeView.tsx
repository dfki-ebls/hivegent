import { FileText, Folder, FolderOpen, type LucideIcon, Pencil, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { DROP_CLASSES, registerTreeRow, type TreeDropState, type TreeItemDrag } from "@/lib/dnd";
import type { DirectoryEntry } from "@/lib/types";
import { cn, collectFileEntries, collectFilePaths } from "@/lib/utils";
import { useSettingsStore } from "@/stores/settings-store";
import { TooltipButton } from "@/components/TooltipButton";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Spinner } from "@/components/ui/spinner";
import {
  FilterToggleButtons,
  type FilterEntryState,
} from "@/components/documents/FilterToggleButtons";
import {
  ChunkCountBadge,
  ExpandButton,
  NameButton,
  REVEAL_ON_HOVER,
} from "@/components/RowButtons";

interface DirectoryTreeViewProps {
  entry: DirectoryEntry;
  /** Source workspace scope of this tree (`~` or `@<group>`), for drag identity. */
  scope: string;
  mutatingPaths: ReadonlySet<string>;
  onEditFile: (path: string) => void;
  onInclude: (path: string) => void;
  onExclude: (path: string) => void;
  /** Current chat-filter state of a path (directories carry a trailing slash). */
  filterState: (path: string) => FilterEntryState;
  /** Delete a file. The other file operations live in the document dialog. */
  onDeleteFile?: (path: string) => void;
  onDeleteDir?: (path: string) => void;
  /** Rename a file (same-directory move to a new basename). */
  onRenameFile?: (path: string) => void;
  onRenameDir?: (path: string) => void;
  selectedFiles?: Set<string>;
  onToggleSelectFile?: (path: string) => void;
  onToggleSelectDir?: (paths: string[]) => void;
  // Drag-and-drop, wired only for writable scopes.
  /** Local dir currently armed as the upload/create target, for highlight. */
  armedDir?: string | null;
  /** Arm a directory (local path) as the target. */
  onArm?: (localDir: string) => void;
  /** Move a dragged selection into a directory (local path, `""` for root). */
  onMoveInto?: (drag: TreeItemDrag, destDir: string) => void;
  /** Upload dropped OS entries into a directory (local path, `""` for root). */
  onUploadInto?: (destDir: string, items: DataTransferItem[], files: File[]) => void;
}

interface FlatRow {
  entry: DirectoryEntry;
  depth: number;
  /**
   * Local dir a drop on this row lands in (`""` for the scope root): its own
   * path for a directory, its parent's for a file — so an expanded directory's
   * whole span is a target for it, at every nesting level.
   */
  dropDir: string;
  isExpanded?: boolean;
  fileCount?: number;
}

// Applied to the row content after the checkbox, so checkboxes stay in a fixed
// column. 28px per level (chevron 24px + gap 4px) lines a child's icon up under
// its parent's folder icon.
function indentStyle(depth: number) {
  return { paddingLeft: `${depth * 28}px` };
}

/** What one row can do in a drag: be a source, a destination, both, or neither. */
export interface TreeRowDnd {
  scope: string;
  resolveDrag: (() => TreeItemDrag) | null;
  destDir: string | null;
  onMove: ((drag: TreeItemDrag) => void) | null;
  onUpload: ((items: DataTransferItem[], files: File[]) => void) | null;
}

/**
 * Register one tree row as a drag source and/or drop target for the lifetime of
 * its element. `resolveDrag`/callbacks are read through effect events so a row
 * only re-registers when its identity changes, not on every render.
 */
function useTreeRowDnd(config: TreeRowDnd) {
  const ref = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const [dropState, setDropState] = useState<TreeDropState>("none");

  const resolveDrag = useEffectEvent(() => config.resolveDrag!());
  const onMove = useEffectEvent((drag: TreeItemDrag) => config.onMove?.(drag));
  const onUpload = useEffectEvent((items: DataTransferItem[], files: File[]) =>
    config.onUpload?.(items, files),
  );

  const draggable = config.resolveDrag != null;
  const { scope, destDir } = config;

  useEffect(() => {
    const element = ref.current;

    if (!element) return;

    return registerTreeRow({
      element,
      drag: draggable ? () => resolveDrag() : null,
      drop:
        destDir == null
          ? null
          : {
              scope,
              destDir,
              onMove: (drag) => onMove(drag),
              onUpload: (items, files) => onUpload(items, files),
            },
      onDragging: setDragging,
      onDropState: setDropState,
    });
  }, [scope, destDir, draggable]);

  return { ref, dragging, dropState };
}

/**
 * The row shell every entry shares, the scope header included: it owns the
 * drag-and-drop wiring and the drag/drop/armed visual state. A fixed min-height
 * keeps the row from growing when its hover buttons appear, and per-row flex
 * (not a shared grid) means one row revealing its buttons never truncates
 * another row's name.
 */
export function TreeRow({
  dnd,
  isArmed = false,
  children,
}: {
  dnd: TreeRowDnd;
  isArmed?: boolean;
  children: ReactNode;
}) {
  const { ref, dragging, dropState } = useTreeRowDnd(dnd);

  return (
    <div
      ref={ref}
      className={cn(
        "group/row flex min-h-9 items-center gap-2 rounded-xl px-2 hover:bg-muted/50",
        dragging && "opacity-50",
        isArmed && "bg-accent",
        DROP_CLASSES[dropState],
      )}
    >
      {children}
    </div>
  );
}

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
  disabled: boolean;
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

/** Subdirectories before files, then by name. Ordering is settled here, at
 * render time, so nothing that produces a tree — the server walk or an
 * optimistic graft — has to agree on it. */
const byKindThenName = (a: DirectoryEntry, b: DirectoryEntry) =>
  Number(a.type === "file") - Number(b.type === "file") || a.name.localeCompare(b.name);

function flattenEntries(
  entry: DirectoryEntry,
  expandedDirs: Set<string>,
  depth = 0,
  parentPath = "",
): FlatRow[] {
  if (entry.type === "file") {
    return [{ entry, depth, dropDir: parentPath }];
  }

  const rows: FlatRow[] = [];

  // Skip the root directory row (rendered externally)
  if (entry.path) {
    const isExpanded = expandedDirs.has(entry.path);
    rows.push({
      entry,
      depth,
      dropDir: entry.path,
      isExpanded,
      fileCount: collectFileEntries(entry).length,
    });

    if (!isExpanded) return rows;
  }

  for (const child of (entry.children ?? []).toSorted(byKindThenName)) {
    rows.push(...flattenEntries(child, expandedDirs, entry.path ? depth + 1 : depth, entry.path));
  }

  return rows;
}

/** The shared row layout: checkbox, the indented name cluster, its actions, then metadata. */
function EntryRow({
  dnd,
  isArmed,
  isMutating,
  depth,
  checkbox,
  name,
  actions,
  meta,
}: {
  dnd: TreeRowDnd;
  isArmed?: boolean;
  isMutating: boolean;
  depth: number;
  checkbox: ReactNode;
  name: ReactNode;
  actions: ReactNode;
  meta: ReactNode;
}) {
  const { t } = useTranslation();

  return (
    <TreeRow dnd={dnd} isArmed={isArmed}>
      {checkbox}
      <div className="flex min-w-0 flex-1 items-center gap-1" style={indentStyle(depth)}>
        {name}
        {isMutating && (
          <Spinner
            aria-label={t(($) => $.common.states.working)}
            className="ml-1 shrink-0 text-muted-foreground"
          />
        )}
      </div>
      <div className="flex shrink-0 items-center gap-0.5">{actions}</div>
      {meta}
    </TreeRow>
  );
}

function FileRow({
  entry,
  dnd,
  isMutating,
  depth,
  onEdit,
  onInclude,
  onExclude,
  filterState,
  onDelete,
  onRename,
  selected,
  onToggleSelect,
}: {
  entry: DirectoryEntry;
  dnd: TreeRowDnd;
  isMutating: boolean;
  depth: number;
  onEdit: () => void;
  onInclude: () => void;
  onExclude: () => void;
  filterState: FilterEntryState;
  onDelete?: () => void;
  onRename?: () => void;
  selected: boolean;
  onToggleSelect?: () => void;
}) {
  const { t } = useTranslation();

  return (
    <EntryRow
      dnd={dnd}
      isMutating={isMutating}
      depth={depth}
      checkbox={
        onToggleSelect && (
          <Checkbox checked={selected} onCheckedChange={onToggleSelect} aria-label={entry.name} />
        )
      }
      name={
        <>
          <FileText className="mr-1 size-4 shrink-0 text-muted-foreground" />
          <NameButton className="font-normal" onClick={onEdit}>
            {entry.name}
          </NameButton>
        </>
      }
      actions={
        <>
          <FilterToggleButtons
            state={filterState}
            onInclude={onInclude}
            onExclude={onExclude}
            revealOnHover
          />
          {onRename && (
            <RowAction
              icon={Pencil}
              label={t(($) => $.common.actions.rename)}
              onClick={onRename}
              disabled={isMutating}
            />
          )}
          {onDelete && (
            <RowAction
              icon={Trash2}
              label={t(($) => $.common.actions.delete)}
              onClick={onDelete}
              disabled={isMutating}
              destructive
            />
          )}
        </>
      }
      meta={entry.chunk_count != null && <ChunkCountBadge count={entry.chunk_count} />}
    />
  );
}

function DirectoryRow({
  entry,
  dnd,
  isExpanded,
  isMutating,
  depth,
  fileCount,
  isArmed,
  selectionState,
  onToggle,
  onSelect,
  onIncludeDir,
  onExcludeDir,
  filterState,
  onDeleteDir,
  onRenameDir,
  onToggleSelect,
}: {
  entry: DirectoryEntry;
  dnd: TreeRowDnd;
  isExpanded: boolean;
  isMutating: boolean;
  depth: number;
  fileCount: number;
  isArmed: boolean;
  selectionState: boolean | "indeterminate";
  onToggle: () => void;
  onSelect?: () => void;
  onIncludeDir: () => void;
  onExcludeDir: () => void;
  filterState: FilterEntryState;
  onDeleteDir?: () => void;
  onRenameDir?: () => void;
  onToggleSelect?: () => void;
}) {
  const { t } = useTranslation();
  const FolderIcon = isExpanded ? FolderOpen : Folder;

  return (
    <EntryRow
      dnd={dnd}
      isArmed={isArmed}
      isMutating={isMutating}
      depth={depth}
      checkbox={
        onToggleSelect && (
          <Checkbox
            checked={selectionState === true}
            indeterminate={selectionState === "indeterminate"}
            onCheckedChange={onToggleSelect}
            aria-label={entry.name}
          />
        )
      }
      // The chevron expands, while the name arms the directory as the upload target.
      name={
        <>
          <ExpandButton expanded={isExpanded} onClick={onToggle} />
          <FolderIcon className="mr-1 size-4 shrink-0 text-muted-foreground" />
          <NameButton
            onClick={onSelect ?? onToggle}
            title={onSelect ? t(($) => $.documents.scope.setUploadTarget) : undefined}
          >
            {entry.name}
          </NameButton>
        </>
      }
      actions={
        <>
          <FilterToggleButtons
            state={filterState}
            onInclude={onIncludeDir}
            onExclude={onExcludeDir}
            revealOnHover
          />
          {onRenameDir && (
            <RowAction
              icon={Pencil}
              label={t(($) => $.documents.confirm.renameFolder)}
              onClick={onRenameDir}
              disabled={isMutating}
            />
          )}
          {onDeleteDir && (
            <RowAction
              icon={Trash2}
              label={t(($) => $.documents.tree.deleteFolder)}
              onClick={onDeleteDir}
              disabled={isMutating}
              destructive
            />
          )}
        </>
      }
      meta={fileCount > 0 && <Badge variant="secondary">{fileCount}</Badge>}
    />
  );
}

export function DirectoryTreeView({
  entry,
  scope,
  mutatingPaths,
  onEditFile,
  onInclude,
  onExclude,
  filterState,
  onDeleteFile,
  onDeleteDir,
  onRenameFile,
  onRenameDir,
  selectedFiles,
  onToggleSelectFile,
  onToggleSelectDir,
  armedDir,
  onArm,
  onMoveInto,
  onUploadInto,
}: DirectoryTreeViewProps) {
  const expandedDirsArray = useSettingsStore((state) => state.expandedDirs);
  const toggleDir = useSettingsStore((state) => state.toggleExpandedDir);
  const expandedDirs = useMemo(() => new Set(expandedDirsArray), [expandedDirsArray]);

  const flatRows = useMemo(() => flattenEntries(entry, expandedDirs), [entry, expandedDirs]);

  // Pre-compute file paths per directory so we walk each subtree once.
  const dirFilePaths = useMemo(() => {
    const map = new Map<string, string[]>();

    for (const row of flatRows) {
      if (row.entry.type === "directory") {
        map.set(row.entry.path, collectFilePaths(row.entry));
      }
    }

    return map;
  }, [flatRows]);

  // Both row kinds drop into their own `dropDir` and differ only in what they
  // drag: a file carries the whole selection when it is part of one, so grabbing
  // any selected row moves them all, while a directory carries itself.
  const rowDnd = (row: FlatRow): TreeRowDnd => ({
    scope,
    resolveDrag: !onMoveInto
      ? null
      : row.entry.type === "file"
        ? () => ({
            scope,
            kind: "file",
            paths:
              selectedFiles && selectedFiles.size > 1 && selectedFiles.has(row.entry.path)
                ? [...selectedFiles]
                : [row.entry.path],
          })
        : () => ({ scope, kind: "directory", paths: [row.entry.path] }),
    destDir: onMoveInto ? row.dropDir : null,
    onMove: onMoveInto ? (drag) => onMoveInto(drag, row.dropDir) : null,
    onUpload: onUploadInto ? (items, files) => onUploadInto(row.dropDir, items, files) : null,
  });

  const renderRow = (row: FlatRow) => {
    const { path } = row.entry;

    if (row.entry.type === "file") {
      return (
        <FileRow
          key={path}
          entry={row.entry}
          dnd={rowDnd(row)}
          isMutating={mutatingPaths.has(path)}
          depth={row.depth}
          onEdit={() => onEditFile(path)}
          onInclude={() => onInclude(path)}
          onExclude={() => onExclude(path)}
          filterState={filterState(path)}
          onDelete={onDeleteFile && (() => onDeleteFile(path))}
          onRename={onRenameFile && (() => onRenameFile(path))}
          selected={selectedFiles?.has(path) ?? false}
          onToggleSelect={onToggleSelectFile && (() => onToggleSelectFile(path))}
        />
      );
    }

    const dirPath = path ? `${path}/` : "";
    const paths = dirFilePaths.get(path) ?? [];
    const selectedCount = selectedFiles ? paths.filter((p) => selectedFiles.has(p)).length : 0;
    const selectionState =
      selectedCount === 0 ? false : selectedCount === paths.length ? true : "indeterminate";

    const armDir =
      onArm &&
      (() => {
        onArm(path);

        if (!row.isExpanded) toggleDir(path);
      });

    return (
      <DirectoryRow
        key={path}
        entry={row.entry}
        dnd={rowDnd(row)}
        isExpanded={row.isExpanded ?? false}
        isMutating={mutatingPaths.has(path)}
        depth={row.depth}
        fileCount={row.fileCount ?? 0}
        isArmed={armedDir === path}
        selectionState={selectionState}
        onToggle={() => toggleDir(path)}
        onSelect={armDir}
        onIncludeDir={() => onInclude(dirPath)}
        onExcludeDir={() => onExclude(dirPath)}
        filterState={filterState(dirPath)}
        onDeleteDir={onDeleteDir && (() => onDeleteDir(path))}
        onRenameDir={onRenameDir && (() => onRenameDir(path))}
        onToggleSelect={onToggleSelectDir && (() => onToggleSelectDir(paths))}
      />
    );
  };

  return <div className="flex flex-col gap-0.5">{flatRows.map(renderRow)}</div>;
}
