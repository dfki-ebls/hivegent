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
import { useCallback } from "react";

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
      className="grid w-full grid-cols-[minmax(0,1fr)_8rem_4rem_3.5rem] items-center gap-x-2 rounded-md px-2 py-1.5 hover:bg-muted/50 group"
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
          className="flex items-center gap-2 min-w-0 text-left cursor-pointer"
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
      <span className="text-right text-xs text-muted-foreground">
        {entry.size_bytes != null ? formatFileSize(entry.size_bytes) : ""}
      </span>
      <div className="flex justify-end">
        {entry.chunk_count != null && (
          <Badge variant="outline" className="text-xs gap-1">
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
      className="grid w-full grid-cols-[minmax(0,1fr)_8rem_4rem_3.5rem] items-center gap-x-2 rounded-md px-2 py-1.5 hover:bg-muted/50 group"
      style={{ paddingLeft: `${depth * 20 + 8}px` }}
    >
      <div className="flex items-center gap-2 min-w-0">
        <button
          type="button"
          className="flex items-center gap-2 min-w-0 text-left cursor-pointer"
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
          <Badge variant="secondary" className="text-xs">
            {fileCount}
          </Badge>
        )}
      </div>
    </div>
  );
}

function countFiles(entry: DirectoryEntry): number {
  if (entry.type === "file") return 1;
  return (entry.children ?? []).reduce((sum, child) => sum + countFiles(child), 0);
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
  const expandedDirs = new Set(expandedDirsArray);

  const toggleDir = useCallback(
    (path: string) => {
      toggleExpandedDir(path);
    },
    [toggleExpandedDir],
  );

  const renderEntry = (child: DirectoryEntry, currentDepth: number) => {
    if (child.type === "file") {
      return (
        <FileRow
          key={child.path}
          entry={child}
          isMutating={mutatingPaths.has(child.path)}
          depth={currentDepth}
          onEdit={() => onEditFile(child.path)}
          onInclude={() => onInclude(child.path)}
          onExclude={() => onExclude(child.path)}
          onReconvert={onReconvert ? () => onReconvert(child.path) : undefined}
          onDownloadOriginal={onDownloadOriginal ? () => onDownloadOriginal(child.path) : undefined}
          onRemove={onRemoveFile ? () => onRemoveFile(child.path) : undefined}
          onMove={onMoveFile ? () => onMoveFile(child.path) : undefined}
          selected={selectedFiles?.has(child.path)}
          onToggleSelect={onToggleSelectFile ? () => onToggleSelectFile(child.path) : undefined}
        />
      );
    }

    const isExpanded = expandedDirs.has(child.path);
    const dirPath = child.path ? `${child.path}/` : "";

    return (
      <div key={child.path}>
        <DirectoryRow
          entry={child}
          isExpanded={isExpanded}
          isMutating={mutatingPaths.has(child.path)}
          depth={currentDepth}
          fileCount={countFiles(child)}
          onToggle={() => toggleDir(child.path)}
          onIncludeDir={() => onInclude(dirPath)}
          onExcludeDir={() => onExclude(dirPath)}
          onCreateSubdir={onCreateSubdir ? () => onCreateSubdir(child.path) : undefined}
          onDeleteDir={onDeleteDir ? () => onDeleteDir(child.path) : undefined}
        />
        {isExpanded &&
          (child.children ?? []).map((grandchild) => renderEntry(grandchild, currentDepth + 1))}
      </div>
    );
  };

  // For the root entry, render its children directly
  if (entry.type === "directory") {
    return (
      <div className="space-y-0.5">
        {(entry.children ?? []).map((child) => renderEntry(child, depth))}
      </div>
    );
  }

  return renderEntry(entry, depth);
}
