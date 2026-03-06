import {
  ChevronDown,
  ChevronRight,
  Download,
  Eye,
  EyeOff,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Move,
  RotateCcw,
  Scissors,
  Trash2,
} from "lucide-react";
import { useCallback, useMemo } from "react";

import type { DirectoryEntry } from "../lib/types";
import { useSettingsStore } from "../stores/settings-store";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Spinner } from "./ui/spinner";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface DirectoryTreeViewProps {
  entry: DirectoryEntry;
  mutatingPaths?: Set<string>;
  onEditFile: (path: string) => void;
  onInclude: (path: string) => void;
  onExclude: (path: string) => void;
  onReconvert?: (path: string) => void;
  onDownloadOriginal?: (path: string) => void;
  onRemoveFile?: (path: string) => void;
  onMoveFile?: (path: string) => void;
  onCreateSubdir?: (parentPath: string) => void;
  onDeleteDir?: (path: string) => void;
  selectedFiles?: Set<string>;
  onToggleSelectFile?: (path: string) => void;
  depth?: number;
}

interface FlatRow {
  entry: DirectoryEntry;
  depth: number;
  isExpanded?: boolean;
  fileCount?: number;
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
  if (depth > 0) {
    const isExpanded = expandedDirs.has(entry.path);
    rows.push({ entry, depth, isExpanded, fileCount: countFiles(entry) });
    if (!isExpanded) return rows;
  }

  for (const child of entry.children ?? []) {
    rows.push(...flattenEntries(child, expandedDirs, depth > 0 ? depth + 1 : depth));
  }

  return rows;
}

function FileRow({
  entry,
  isMutating,
  depth,
  onEdit,
  onInclude,
  onExclude,
  onReconvert,
  onDownloadOriginal,
  onRemove,
  onMove,
  selected,
  onToggleSelect,
}: {
  entry: DirectoryEntry;
  isMutating: boolean;
  depth: number;
  onEdit: () => void;
  onInclude: () => void;
  onExclude: () => void;
  onReconvert?: () => void;
  onDownloadOriginal?: () => void;
  onRemove?: () => void;
  onMove?: () => void;
  selected?: boolean;
  onToggleSelect?: () => void;
}) {
  return (
    <div
      className="col-span-full grid grid-cols-[subgrid] items-center rounded-md px-2 py-1.5 hover:bg-muted/50 group"
      style={{ paddingLeft: `${depth * 20 + 8}px` }}
    >
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
          onClick={onEdit}
        >
          <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate text-sm">{entry.name}</span>
        </button>
        {isMutating && <Spinner className="size-3 shrink-0 text-muted-foreground" />}
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
        {entry.has_original && onReconvert && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Reconvert from original"
            onClick={onReconvert}
            disabled={isMutating}
          >
            <RotateCcw className="h-3 w-3" />
          </Button>
        )}
        {entry.has_original && onDownloadOriginal && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Download original"
            onClick={onDownloadOriginal}
            disabled={isMutating}
          >
            <Download className="h-3 w-3" />
          </Button>
        )}
        {onMove && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Move"
            onClick={onMove}
            disabled={isMutating}
          >
            <Move className="h-3 w-3" />
          </Button>
        )}
        {onRemove && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Delete"
            onClick={onRemove}
            disabled={isMutating}
          >
            <Trash2 className="h-3 w-3 text-destructive" />
          </Button>
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
    </div>
  );
}

function DirectoryRow({
  entry,
  isExpanded,
  isMutating,
  depth,
  fileCount,
  onToggle,
  onIncludeDir,
  onExcludeDir,
  onCreateSubdir,
  onDeleteDir,
}: {
  entry: DirectoryEntry;
  isExpanded: boolean;
  isMutating: boolean;
  depth: number;
  fileCount: number;
  onToggle: () => void;
  onIncludeDir: () => void;
  onExcludeDir: () => void;
  onCreateSubdir?: () => void;
  onDeleteDir?: () => void;
}) {
  const FolderIcon = isExpanded ? FolderOpen : Folder;
  const ChevronIcon = isExpanded ? ChevronDown : ChevronRight;

  return (
    <div
      className="col-span-full grid grid-cols-[subgrid] items-center rounded-md px-2 py-1.5 hover:bg-muted/50 group"
      style={{ paddingLeft: `${depth * 20 + 8}px` }}
    >
      <div className="flex items-center gap-2 min-w-0">
        <button
          type="button"
          className="flex flex-1 items-center gap-2 min-w-0 text-left cursor-pointer"
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
  onEditFile,
  onInclude,
  onExclude,
  onReconvert,
  onDownloadOriginal,
  onRemoveFile,
  onMoveFile,
  onCreateSubdir,
  onDeleteDir,
  selectedFiles,
  onToggleSelectFile,
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

  const renderRow = (row: FlatRow) => {
    if (row.entry.type === "file") {
      return (
        <FileRow
          key={row.entry.path}
          entry={row.entry}
          isMutating={mutatingPaths.has(row.entry.path)}
          depth={row.depth}
          onEdit={() => onEditFile(row.entry.path)}
          onInclude={() => onInclude(row.entry.path)}
          onExclude={() => onExclude(row.entry.path)}
          onReconvert={onReconvert ? () => onReconvert(row.entry.path) : undefined}
          onDownloadOriginal={
            onDownloadOriginal ? () => onDownloadOriginal(row.entry.path) : undefined
          }
          onRemove={onRemoveFile ? () => onRemoveFile(row.entry.path) : undefined}
          onMove={onMoveFile ? () => onMoveFile(row.entry.path) : undefined}
          selected={selectedFiles?.has(row.entry.path)}
          onToggleSelect={onToggleSelectFile ? () => onToggleSelectFile(row.entry.path) : undefined}
        />
      );
    }

    const dirPath = row.entry.path ? `${row.entry.path}/` : "";

    return (
      <DirectoryRow
        key={row.entry.path}
        entry={row.entry}
        isExpanded={row.isExpanded ?? false}
        isMutating={mutatingPaths.has(row.entry.path)}
        depth={row.depth}
        fileCount={row.fileCount ?? 0}
        onToggle={() => toggleDir(row.entry.path)}
        onIncludeDir={() => onInclude(dirPath)}
        onExcludeDir={() => onExclude(dirPath)}
        onCreateSubdir={onCreateSubdir ? () => onCreateSubdir(row.entry.path) : undefined}
        onDeleteDir={onDeleteDir ? () => onDeleteDir(row.entry.path) : undefined}
      />
    );
  };

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] gap-x-3 gap-y-0.5">
      {flatRows.map(renderRow)}
    </div>
  );
}
