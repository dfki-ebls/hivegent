import {
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  type LucideIcon,
  Scissors,
  Trash2,
} from "lucide-react";
import {
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  FilterToggleButtons,
  type FilterEntryState,
} from "@/components/documents/FilterToggleButtons";
import { DOCUMENT_ACTIONS, type DocumentActionId } from "@/lib/document-actions";
import { DROP_CLASSES, registerTreeRow, type TreeDropState, type TreeItemDrag } from "@/lib/dnd";
import type { DirectoryEntry } from "@/lib/types";
import { cn, collectFilePaths, formatFileSize } from "@/lib/utils";
import { useSettingsStore } from "@/stores/settings-store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Spinner } from "@/components/ui/spinner";

interface DirectoryTreeViewProps {
  entry: DirectoryEntry;
  /** Source workspace scope of this tree (`~` or `@<group>`), for drag identity. */
  scope: string;
  mutatingPaths?: Set<string>;
  onEditFile: (path: string) => void;
  onInclude: (path: string) => void;
  onExclude: (path: string) => void;
  /** Current chat-filter state of a path (directories carry a trailing slash). */
  filterState: (path: string) => FilterEntryState;
  /** Dispatched for file-operation actions (rechunk, reconvert, download, delete). */
  onFileAction?: (path: string, actionId: DocumentActionId) => void;
  onCreateSubdir?: (parentPath: string) => void;
  onDeleteDir?: (path: string) => void;
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
  depth?: number;
}

interface FlatRow {
  entry: DirectoryEntry;
  depth: number;
  isExpanded?: boolean;
  fileCount?: number;
}

// Applied to the row content after the checkbox, so checkboxes stay in a fixed column
// under the section header's chevron. 24px per level (icon 16px + gap 8px) lines a
// child's icon up under its parent's folder icon.
function indentStyle(depth: number): CSSProperties {
  return { paddingLeft: `${depth * 24}px` };
}

/** What one row can do in a drag: be a source, a destination, both, or neither. */
interface TreeRowDnd {
  scope: string;
  resolveDrag: (() => TreeItemDrag) | null;
  destDir: string | null;
  onMove: ((drag: TreeItemDrag) => void) | null;
  onUpload: ((items: DataTransferItem[], files: File[]) => void) | null;
}

/**
 * Register one tree row as a drag source and/or drop target for the lifetime of
 * its element. `resolveDrag`/callbacks are read through a ref so a row only
 * re-registers when its identity changes, not on every render.
 */
function useTreeRowDnd(config: TreeRowDnd) {
  const ref = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const [dropState, setDropState] = useState<TreeDropState>("none");

  const latest = useRef(config);
  latest.current = config;

  const draggable = config.resolveDrag != null;
  const { scope, destDir } = config;

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    return registerTreeRow({
      element,
      drag: draggable ? () => latest.current.resolveDrag!() : null,
      drop:
        destDir == null
          ? null
          : {
              scope,
              destDir,
              onMove: (drag) => latest.current.onMove?.(drag),
              onUpload: (items, files) => latest.current.onUpload?.(items, files),
            },
      onDragging: setDragging,
      onDropState: setDropState,
    });
  }, [scope, destDir, draggable]);

  return { ref, dragging, dropState };
}

// The subgrid row shell every entry shares: it owns the drag-and-drop wiring and
// the drag/drop/armed visual state, so a file and a directory row differ only in
// the four column cells they pass as children.
function TreeRow({
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
        "col-span-full grid grid-cols-[subgrid] items-center rounded-md px-2 py-1.5 hover:bg-muted/50 group",
        dragging && "opacity-50",
        isArmed && "bg-accent",
        DROP_CLASSES[dropState],
      )}
    >
      {children}
    </div>
  );
}

// The first column: a fixed checkbox slot, the indented label, and a trailing
// mutating spinner — shared so files and directories agree on that layout.
function RowMain({
  checkbox,
  isMutating,
  children,
}: {
  checkbox: ReactNode;
  isMutating: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      {checkbox}
      {children}
      {isMutating && <Spinner className="size-3 shrink-0 text-muted-foreground" />}
    </div>
  );
}

// A hover-revealed icon action (rechunk, delete, create-subdir, …), identical
// across file and directory rows apart from its icon, label, and handler.
function RowActionButton({
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
    <Button
      variant="ghost"
      size="icon"
      className="h-6 w-6 opacity-0 group-hover:opacity-100"
      title={label}
      onClick={onClick}
      disabled={disabled}
    >
      <Icon className={cn("h-3 w-3", destructive && "text-destructive")} />
    </Button>
  );
}

function countFiles(entry: DirectoryEntry): number {
  if (entry.type === "file") return 1;
  return (entry.children ?? []).reduce((sum, child) => sum + countFiles(child), 0);
}

function flattenEntries(
  entry: DirectoryEntry,
  expandedDirs: Set<string>,
  depth: number,
): FlatRow[] {
  if (entry.type === "file") {
    return [{ entry, depth }];
  }

  const rows: FlatRow[] = [];

  // Skip the root directory row (rendered externally)
  if (entry.path) {
    const isExpanded = expandedDirs.has(entry.path);
    rows.push({ entry, depth, isExpanded, fileCount: countFiles(entry) });
    if (!isExpanded) return rows;
  }

  for (const child of entry.children ?? []) {
    rows.push(...flattenEntries(child, expandedDirs, entry.path ? depth + 1 : depth));
  }

  return rows;
}

function FileRow({
  entry,
  scope,
  isMutating,
  depth,
  onEdit,
  onInclude,
  onExclude,
  filterState,
  onAction,
  selected,
  onToggleSelect,
  resolveDrag,
}: {
  entry: DirectoryEntry;
  scope: string;
  isMutating: boolean;
  depth: number;
  onEdit: () => void;
  onInclude: () => void;
  onExclude: () => void;
  filterState: FilterEntryState;
  onAction?: (actionId: DocumentActionId) => void;
  selected?: boolean;
  onToggleSelect?: () => void;
  resolveDrag: (() => TreeItemDrag) | null;
}) {
  return (
    // Files are drag sources only — you move a file, you never drop onto one.
    <TreeRow dnd={{ scope, resolveDrag, destDir: null, onMove: null, onUpload: null }}>
      <RowMain
        isMutating={isMutating}
        checkbox={
          onToggleSelect && (
            <Checkbox
              checked={selected ?? false}
              onCheckedChange={() => onToggleSelect()}
              className="shrink-0"
            />
          )
        }
      >
        <button
          type="button"
          className="flex flex-1 items-center gap-2 min-w-0 text-left cursor-pointer"
          style={indentStyle(depth)}
          onClick={onEdit}
        >
          <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate text-sm">{entry.name}</span>
        </button>
      </RowMain>
      <div className="flex gap-0.5">
        <FilterToggleButtons
          state={filterState}
          onInclude={onInclude}
          onExclude={onExclude}
          compact
          revealOnHover
        />
        {onAction &&
          DOCUMENT_ACTIONS.map((action) =>
            action.requiresOriginal && !entry.has_original ? null : (
              <RowActionButton
                key={action.id}
                icon={action.icon}
                label={action.label}
                onClick={() => onAction(action.id)}
                disabled={isMutating}
                destructive={action.variant === "destructive"}
              />
            ),
          )}
      </div>
      <span className="text-right text-xs text-muted-foreground whitespace-nowrap">
        {entry.size_bytes != null ? formatFileSize(entry.size_bytes) : ""}
      </span>
      <div className="flex justify-end">
        {entry.chunk_count != null && (
          <Badge variant="outline" className="text-xs gap-1 whitespace-nowrap">
            <Scissors className="h-3 w-3" />
            {entry.chunk_count}
          </Badge>
        )}
      </div>
    </TreeRow>
  );
}

function DirectoryRow({
  entry,
  scope,
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
  onCreateSubdir,
  onDeleteDir,
  onToggleSelect,
  resolveDrag,
  onMoveInto,
  onUploadInto,
}: {
  entry: DirectoryEntry;
  scope: string;
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
  onCreateSubdir?: () => void;
  onDeleteDir?: () => void;
  onToggleSelect?: () => void;
  resolveDrag: (() => TreeItemDrag) | null;
  onMoveInto: ((drag: TreeItemDrag) => void) | null;
  onUploadInto: ((items: DataTransferItem[], files: File[]) => void) | null;
}) {
  // Directory rows are drop targets exactly when the scope is writable, which is
  // also when they are draggable — the caller gates both on the same condition.
  const dndEnabled = resolveDrag != null;
  const FolderIcon = isExpanded ? FolderOpen : Folder;
  const ChevronIcon = isExpanded ? ChevronDown : ChevronRight;

  return (
    <TreeRow
      isArmed={isArmed}
      dnd={{
        scope,
        resolveDrag,
        destDir: dndEnabled ? entry.path : null,
        onMove: onMoveInto,
        onUpload: onUploadInto,
      }}
    >
      <RowMain
        isMutating={isMutating}
        checkbox={
          onToggleSelect && (
            <Checkbox
              checked={selectionState}
              onCheckedChange={() => onToggleSelect()}
              className="shrink-0"
            />
          )
        }
      >
        {/* Indent wraps chevron + name together so a child's folder icon lines
            up under its parent's, while the two clicks stay separate: the
            chevron expands, the name arms the directory as the upload target. */}
        <div className="flex flex-1 items-center gap-2 min-w-0" style={indentStyle(depth)}>
          <button
            type="button"
            className="shrink-0 cursor-pointer text-muted-foreground"
            title={isExpanded ? "Collapse" : "Expand"}
            onClick={onToggle}
          >
            <ChevronIcon className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="flex flex-1 items-center gap-2 min-w-0 text-left cursor-pointer"
            onClick={onSelect ?? onToggle}
            title={onSelect ? "Set as upload target" : undefined}
          >
            <FolderIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate text-sm font-medium">{entry.name}</span>
          </button>
        </div>
      </RowMain>
      <div className="flex gap-0.5">
        <FilterToggleButtons
          state={filterState}
          onInclude={onIncludeDir}
          onExclude={onExcludeDir}
          compact
          revealOnHover
        />
        {onCreateSubdir && (
          <RowActionButton
            icon={FolderPlus}
            label="Create subdirectory"
            onClick={onCreateSubdir}
            disabled={isMutating}
          />
        )}
        {onDeleteDir && (
          <RowActionButton
            icon={Trash2}
            label="Delete directory"
            onClick={onDeleteDir}
            disabled={isMutating}
            destructive
          />
        )}
      </div>
      <span />
      <div className="flex justify-end">
        {fileCount > 0 && (
          <Badge variant="secondary" className="text-xs whitespace-nowrap">
            {fileCount}
          </Badge>
        )}
      </div>
    </TreeRow>
  );
}

const EMPTY_SET = new Set<string>();

export function DirectoryTreeView({
  entry,
  scope,
  mutatingPaths = EMPTY_SET,
  onEditFile,
  onInclude,
  onExclude,
  filterState,
  onFileAction,
  onCreateSubdir,
  onDeleteDir,
  selectedFiles,
  onToggleSelectFile,
  onToggleSelectDir,
  armedDir,
  onArm,
  onMoveInto,
  onUploadInto,
  depth = 0,
}: DirectoryTreeViewProps) {
  const expandedDirsArray = useSettingsStore((state) => state.expandedDirs);
  const toggleExpandedDir = useSettingsStore((state) => state.toggleExpandedDir);
  const expandedDirs = useMemo(() => new Set(expandedDirsArray), [expandedDirsArray]);

  const toggleDir = useCallback(
    (path: string) => {
      toggleExpandedDir(path);
    },
    [toggleExpandedDir],
  );

  const flatRows = useMemo(
    () => flattenEntries(entry, expandedDirs, depth),
    [entry, expandedDirs, depth],
  );

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

  // A dragged file carries the whole selection when it is part of it, so
  // grabbing any selected row moves them all; otherwise it carries just itself.
  const resolveFileDrag = useCallback(
    (path: string): (() => TreeItemDrag) | null => {
      if (!onMoveInto) return null;
      return () => {
        const paths =
          selectedFiles && selectedFiles.size > 1 && selectedFiles.has(path)
            ? [...selectedFiles]
            : [path];
        return { scope, kind: "file", paths };
      };
    },
    [onMoveInto, selectedFiles, scope],
  );

  const renderRow = (row: FlatRow) => {
    if (row.entry.type === "file") {
      const fileMutating = mutatingPaths.has(row.entry.path);
      return (
        <FileRow
          key={row.entry.path}
          entry={row.entry}
          scope={scope}
          isMutating={fileMutating}
          depth={row.depth}
          onEdit={() => onEditFile(row.entry.path)}
          onInclude={() => onInclude(row.entry.path)}
          onExclude={() => onExclude(row.entry.path)}
          filterState={filterState(row.entry.path)}
          onAction={onFileAction ? (actionId) => onFileAction(row.entry.path, actionId) : undefined}
          selected={selectedFiles?.has(row.entry.path)}
          onToggleSelect={onToggleSelectFile ? () => onToggleSelectFile(row.entry.path) : undefined}
          resolveDrag={resolveFileDrag(row.entry.path)}
        />
      );
    }

    const dirPath = row.entry.path ? `${row.entry.path}/` : "";
    const paths = dirFilePaths.get(row.entry.path) ?? [];

    let selectionState: boolean | "indeterminate" = false;
    if (selectedFiles && paths.length > 0) {
      let count = 0;
      for (const p of paths) {
        if (selectedFiles.has(p)) count++;
      }
      selectionState = count === 0 ? false : count === paths.length ? true : "indeterminate";
    }

    const armDir = onArm
      ? () => {
          onArm(row.entry.path);
          if (!row.isExpanded) toggleDir(row.entry.path);
        }
      : undefined;

    return (
      <DirectoryRow
        key={row.entry.path}
        entry={row.entry}
        scope={scope}
        isExpanded={row.isExpanded ?? false}
        isMutating={mutatingPaths.has(row.entry.path)}
        depth={row.depth}
        fileCount={row.fileCount ?? 0}
        isArmed={armedDir === row.entry.path}
        selectionState={selectionState}
        onToggle={() => toggleDir(row.entry.path)}
        onSelect={armDir}
        onIncludeDir={() => onInclude(dirPath)}
        onExcludeDir={() => onExclude(dirPath)}
        filterState={filterState(dirPath)}
        onCreateSubdir={onCreateSubdir ? () => onCreateSubdir(row.entry.path) : undefined}
        onDeleteDir={onDeleteDir ? () => onDeleteDir(row.entry.path) : undefined}
        onToggleSelect={onToggleSelectDir ? () => onToggleSelectDir(paths) : undefined}
        resolveDrag={onMoveInto ? () => ({ scope, kind: "directory", paths: [row.entry.path] }) : null}
        onMoveInto={onMoveInto ? (drag) => onMoveInto(drag, row.entry.path) : null}
        onUploadInto={
          onUploadInto ? (items, files) => onUploadInto(row.entry.path, items, files) : null
        }
      />
    );
  };

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] gap-x-3 gap-y-0.5">
      {flatRows.map(renderRow)}
    </div>
  );
}
