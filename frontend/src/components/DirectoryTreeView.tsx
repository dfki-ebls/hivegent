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
  RotateCcw,
  Scissors,
  Trash2,
} from "lucide-react";
import { useCallback } from "react";

import type { DirectoryEntry } from "../lib/types";
import { useSettingsStore } from "../stores/settings-store";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface DirectoryTreeViewProps {
  entry: DirectoryEntry;
  isLoading: boolean;
  onEditFile: (path: string) => void;
  onInclude: (path: string) => void;
  onExclude: (path: string) => void;
  onReconvert?: (path: string) => void;
  onRemoveFile?: (path: string) => void;
  onMoveFile?: (path: string) => void;
  onCreateSubdir?: (parentPath: string) => void;
  onDeleteDir?: (path: string) => void;
  depth?: number;
}

function FileRow({
  entry,
  isLoading,
  depth,
  onEdit,
  onInclude,
  onExclude,
  onReconvert,
  onRemove,
  onMove,
}: {
  entry: DirectoryEntry;
  isLoading: boolean;
  depth: number;
  onEdit: () => void;
  onInclude: () => void;
  onExclude: () => void;
  onReconvert?: () => void;
  onRemove?: () => void;
  onMove?: () => void;
}) {
  return (
    <button
      type="button"
      className="grid w-full grid-cols-[minmax(0,1fr)_8rem_4rem_3.5rem] items-center gap-x-2 rounded-md px-2 py-1.5 hover:bg-muted/50 cursor-pointer group text-left"
      style={{ paddingLeft: `${depth * 20 + 8}px` }}
      onClick={onEdit}
    >
      <div className="flex items-center gap-2 min-w-0">
        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 truncate text-sm">{entry.name}</span>
      </div>
      <div className="flex gap-0.5 opacity-0 group-hover:opacity-100">
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          title="Include in chat"
          onClick={(e) => {
            e.stopPropagation();
            onInclude();
          }}
          disabled={isLoading}
        >
          <Eye className="h-3 w-3" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          title="Exclude from chat"
          onClick={(e) => {
            e.stopPropagation();
            onExclude();
          }}
          disabled={isLoading}
        >
          <EyeOff className="h-3 w-3" />
        </Button>
        {entry.has_original && onReconvert && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Reconvert from original"
            onClick={(e) => {
              e.stopPropagation();
              onReconvert();
            }}
            disabled={isLoading}
          >
            <RotateCcw className="h-3 w-3" />
          </Button>
        )}
        {onMove && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Move"
            onClick={(e) => {
              e.stopPropagation();
              onMove();
            }}
            disabled={isLoading}
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
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            disabled={isLoading}
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
    </button>
  );
}

function DirectoryRow({
  entry,
  isExpanded,
  isLoading,
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
  isLoading: boolean;
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
    <button
      type="button"
      className="grid w-full grid-cols-[minmax(0,1fr)_8rem_4rem_3.5rem] items-center gap-x-2 rounded-md px-2 py-1.5 hover:bg-muted/50 cursor-pointer group text-left"
      style={{ paddingLeft: `${depth * 20 + 8}px` }}
      onClick={onToggle}
    >
      <div className="flex items-center gap-2 min-w-0">
        <ChevronIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <FolderIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 truncate text-sm font-medium">{entry.name}</span>
      </div>
      <div className="flex gap-0.5 opacity-0 group-hover:opacity-100">
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          title="Include directory in chat"
          onClick={(e) => {
            e.stopPropagation();
            onIncludeDir();
          }}
          disabled={isLoading}
        >
          <Eye className="h-3 w-3" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          title="Exclude directory from chat"
          onClick={(e) => {
            e.stopPropagation();
            onExcludeDir();
          }}
          disabled={isLoading}
        >
          <EyeOff className="h-3 w-3" />
        </Button>
        {onCreateSubdir && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Create subdirectory"
            onClick={(e) => {
              e.stopPropagation();
              onCreateSubdir();
            }}
            disabled={isLoading}
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
            onClick={(e) => {
              e.stopPropagation();
              onDeleteDir();
            }}
            disabled={isLoading}
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
    </button>
  );
}

function countFiles(entry: DirectoryEntry): number {
  if (entry.type === "file") return 1;
  return (entry.children ?? []).reduce((sum, child) => sum + countFiles(child), 0);
}

export function DirectoryTreeView({
  entry,
  isLoading,
  onEditFile,
  onInclude,
  onExclude,
  onReconvert,
  onRemoveFile,
  onMoveFile,
  onCreateSubdir,
  onDeleteDir,
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
          isLoading={isLoading}
          depth={currentDepth}
          onEdit={() => onEditFile(child.path)}
          onInclude={() => onInclude(child.path)}
          onExclude={() => onExclude(child.path)}
          onReconvert={onReconvert ? () => onReconvert(child.path) : undefined}
          onRemove={onRemoveFile ? () => onRemoveFile(child.path) : undefined}
          onMove={onMoveFile ? () => onMoveFile(child.path) : undefined}
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
          isLoading={isLoading}
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
