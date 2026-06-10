import {
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Move,
  Scissors,
  Trash2,
} from "lucide-react";
import { type CSSProperties, useCallback, useMemo } from "react";

import { DOCUMENT_ACTIONS, type DocumentActionId } from "@/lib/document-actions";
import type { DirectoryEntry, OperationStage } from "@/lib/types";
import { collectFilePaths, formatFileSize } from "@/lib/utils";
import { useSettingsStore } from "@/stores/settings-store";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Spinner } from "./ui/spinner";

interface DirectoryTreeViewProps {
  entry: DirectoryEntry;
  mutatingPaths?: Set<string>;
  operationStage?: OperationStage | null;
  onEditFile: (path: string) => void;
  onInclude: (path: string) => void;
  onExclude: (path: string) => void;
  /** Dispatched for file-operation actions (rechunk, reconvert, download, move, delete). */
  onFileAction?: (path: string, actionId: DocumentActionId) => void;
  onCreateSubdir?: (parentPath: string) => void;
  onDeleteDir?: (path: string) => void;
  onMoveDir?: (path: string) => void;
  selectedFiles?: Set<string>;
  onToggleSelectFile?: (path: string) => void;
  onToggleSelectDir?: (paths: string[]) => void;
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
  isMutating,
  operationStage,
  depth,
  onEdit,
  onInclude,
  onExclude,
  onAction,
  selected,
  onToggleSelect,
}: {
  entry: DirectoryEntry;
  isMutating: boolean;
  operationStage?: OperationStage | null;
  depth: number;
  onEdit: () => void;
  onInclude: () => void;
  onExclude: () => void;
  onAction?: (actionId: DocumentActionId) => void;
  selected?: boolean;
  onToggleSelect?: () => void;
}) {
  return (
    <div className="col-span-full grid grid-cols-[subgrid] items-center rounded-md px-2 py-1.5 hover:bg-muted/50 group">
      <div className="flex items-center gap-2 min-w-0">
        {onToggleSelect && (
          <Checkbox
            checked={selected ?? false}
            onCheckedChange={() => onToggleSelect()}
            className="shrink-0"
          />
        )}
        <button
          type="button"
          className="flex flex-1 items-center gap-2 min-w-0 text-left cursor-pointer"
          style={indentStyle(depth)}
          onClick={onEdit}
        >
          <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate text-sm">{entry.name}</span>
        </button>
        {isMutating && <Spinner className="size-3 shrink-0 text-muted-foreground" />}
        {isMutating && operationStage && (
          <span className="truncate text-xs text-muted-foreground">{operationStage.stage}...</span>
        )}
      </div>
      <div className="flex gap-0.5 opacity-0 group-hover:opacity-100">
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          title="Include in chat"
          onClick={onInclude}
        >
          <Eye className="h-3 w-3" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          title="Exclude from chat"
          onClick={onExclude}
        >
          <EyeOff className="h-3 w-3" />
        </Button>
        {onAction &&
          DOCUMENT_ACTIONS.map((action) => {
            if (action.requiresOriginal && !entry.has_original) return null;
            const Icon = action.icon;
            return (
              <Button
                key={action.id}
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                title={action.label}
                onClick={() => onAction(action.id)}
                disabled={isMutating}
              >
                <Icon
                  className={`h-3 w-3${action.variant === "destructive" ? " text-destructive" : ""}`}
                />
              </Button>
            );
          })}
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
    </div>
  );
}

function DirectoryRow({
  entry,
  isExpanded,
  isMutating,
  depth,
  fileCount,
  selectionState,
  onToggle,
  onIncludeDir,
  onExcludeDir,
  onCreateSubdir,
  onDeleteDir,
  onMoveDir,
  onToggleSelect,
}: {
  entry: DirectoryEntry;
  isExpanded: boolean;
  isMutating: boolean;
  depth: number;
  fileCount: number;
  selectionState: boolean | "indeterminate";
  onToggle: () => void;
  onIncludeDir: () => void;
  onExcludeDir: () => void;
  onCreateSubdir?: () => void;
  onDeleteDir?: () => void;
  onMoveDir?: () => void;
  onToggleSelect?: () => void;
}) {
  const FolderIcon = isExpanded ? FolderOpen : Folder;
  const ChevronIcon = isExpanded ? ChevronDown : ChevronRight;

  return (
    <div className="col-span-full grid grid-cols-[subgrid] items-center rounded-md px-2 py-1.5 hover:bg-muted/50 group">
      <div className="flex items-center gap-2 min-w-0">
        {onToggleSelect && (
          <Checkbox
            checked={selectionState}
            onCheckedChange={() => onToggleSelect()}
            className="shrink-0"
          />
        )}
        <button
          type="button"
          className="flex flex-1 items-center gap-2 min-w-0 text-left cursor-pointer"
          style={indentStyle(depth)}
          onClick={onToggle}
        >
          <ChevronIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
          <FolderIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate text-sm font-medium">{entry.name}</span>
        </button>
        {isMutating && <Spinner className="size-3 shrink-0 text-muted-foreground" />}
      </div>
      <div className="flex gap-0.5 opacity-0 group-hover:opacity-100">
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          title="Include directory in chat"
          onClick={onIncludeDir}
        >
          <Eye className="h-3 w-3" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          title="Exclude directory from chat"
          onClick={onExcludeDir}
        >
          <EyeOff className="h-3 w-3" />
        </Button>
        {onCreateSubdir && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Create subdirectory"
            onClick={onCreateSubdir}
            disabled={isMutating}
          >
            <FolderPlus className="h-3 w-3" />
          </Button>
        )}
        {onMoveDir && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Move directory"
            onClick={onMoveDir}
            disabled={isMutating}
          >
            <Move className="h-3 w-3" />
          </Button>
        )}
        {onDeleteDir && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Delete directory"
            onClick={onDeleteDir}
            disabled={isMutating}
          >
            <Trash2 className="h-3 w-3 text-destructive" />
          </Button>
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
    </div>
  );
}

const EMPTY_SET = new Set<string>();

export function DirectoryTreeView({
  entry,
  mutatingPaths = EMPTY_SET,
  operationStage,
  onEditFile,
  onInclude,
  onExclude,
  onFileAction,
  onCreateSubdir,
  onDeleteDir,
  onMoveDir,
  selectedFiles,
  onToggleSelectFile,
  onToggleSelectDir,
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

  const renderRow = (row: FlatRow) => {
    if (row.entry.type === "file") {
      const fileMutating = mutatingPaths.has(row.entry.path);
      return (
        <FileRow
          key={row.entry.path}
          entry={row.entry}
          isMutating={fileMutating}
          operationStage={fileMutating ? operationStage : null}
          depth={row.depth}
          onEdit={() => onEditFile(row.entry.path)}
          onInclude={() => onInclude(row.entry.path)}
          onExclude={() => onExclude(row.entry.path)}
          onAction={onFileAction ? (actionId) => onFileAction(row.entry.path, actionId) : undefined}
          selected={selectedFiles?.has(row.entry.path)}
          onToggleSelect={onToggleSelectFile ? () => onToggleSelectFile(row.entry.path) : undefined}
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

    return (
      <DirectoryRow
        key={row.entry.path}
        entry={row.entry}
        isExpanded={row.isExpanded ?? false}
        isMutating={mutatingPaths.has(row.entry.path)}
        depth={row.depth}
        fileCount={row.fileCount ?? 0}
        selectionState={selectionState}
        onToggle={() => toggleDir(row.entry.path)}
        onIncludeDir={() => onInclude(dirPath)}
        onExcludeDir={() => onExclude(dirPath)}
        onCreateSubdir={onCreateSubdir ? () => onCreateSubdir(row.entry.path) : undefined}
        onDeleteDir={onDeleteDir ? () => onDeleteDir(row.entry.path) : undefined}
        onMoveDir={onMoveDir ? () => onMoveDir(row.entry.path) : undefined}
        onToggleSelect={onToggleSelectDir ? () => onToggleSelectDir(paths) : undefined}
      />
    );
  };

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] gap-x-3 gap-y-0.5">
      {flatRows.map(renderRow)}
    </div>
  );
}
