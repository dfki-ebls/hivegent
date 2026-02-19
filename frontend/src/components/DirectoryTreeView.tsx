import {
  ChevronDown,
  ChevronRight,
  EyeOff,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  MessageSquarePlus,
  Move,
  RefreshCw,
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
  onViewChunks: (path: string) => void;
  onRechunk: (path: string) => void;
  onReconvert: (path: string) => void;
  onRemoveFile: (path: string) => void;
  onMoveFile: (path: string) => void;
  onCreateSubdir: (parentPath: string) => void;
  onDeleteDir: (path: string) => void;
  depth?: number;
}

function FileRow({
  entry,
  isLoading,
  depth,
  onEdit,
  onInclude,
  onExclude,
  onViewChunks,
  onRechunk,
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
  onViewChunks: () => void;
  onRechunk: () => void;
  onReconvert: () => void;
  onRemove: () => void;
  onMove: () => void;
}) {
  return (
    // biome-ignore lint/a11y/useSemanticElements: contains nested interactive elements
    <div
      role="button"
      tabIndex={0}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted/50 cursor-pointer group"
      style={{ paddingLeft: `${depth * 20 + 8}px` }}
      onClick={onEdit}
      onKeyDown={(e) => {
        if (e.key === "Enter") onEdit();
      }}
    >
      <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate text-sm">{entry.name}</span>
      {entry.size_bytes != null && (
        <span className="shrink-0 text-xs text-muted-foreground">
          {formatFileSize(entry.size_bytes)}
        </span>
      )}
      {entry.chunk_count != null && (
        <Badge variant="outline" className="shrink-0 text-xs gap-1">
          <Scissors className="h-3 w-3" />
          {entry.chunk_count}
        </Badge>
      )}
      <div className="hidden gap-0.5 group-hover:flex">
        {entry.chunk_count != null && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="View chunks"
            onClick={(e) => {
              e.stopPropagation();
              onViewChunks();
            }}
            disabled={isLoading}
          >
            <Scissors className="h-3 w-3" />
          </Button>
        )}
        {entry.chunk_count != null && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            title="Rechunk"
            onClick={(e) => {
              e.stopPropagation();
              onRechunk();
            }}
            disabled={isLoading}
          >
            <RefreshCw className="h-3 w-3" />
          </Button>
        )}
        {entry.has_original && (
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
          <MessageSquarePlus className="h-3 w-3" />
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
      </div>
    </div>
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
  onCreateSubdir: () => void;
  onDeleteDir: () => void;
}) {
  const FolderIcon = isExpanded ? FolderOpen : Folder;
  const ChevronIcon = isExpanded ? ChevronDown : ChevronRight;

  return (
    // biome-ignore lint/a11y/useSemanticElements: contains nested interactive elements
    <div
      role="button"
      tabIndex={0}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted/50 cursor-pointer group"
      style={{ paddingLeft: `${depth * 20 + 8}px` }}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === "Enter") onToggle();
      }}
    >
      <ChevronIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
      <FolderIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate text-sm font-medium">
        {entry.name}
      </span>
      {fileCount > 0 && (
        <Badge variant="secondary" className="shrink-0 text-xs">
          {fileCount}
        </Badge>
      )}
      <div className="hidden gap-0.5 group-hover:flex">
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
          <MessageSquarePlus className="h-3 w-3" />
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
      </div>
    </div>
  );
}

function countFiles(entry: DirectoryEntry): number {
  if (entry.type === "file") return 1;
  return (entry.children ?? []).reduce(
    (sum, child) => sum + countFiles(child),
    0,
  );
}

export function DirectoryTreeView({
  entry,
  isLoading,
  onEditFile,
  onInclude,
  onExclude,
  onViewChunks,
  onRechunk,
  onReconvert,
  onRemoveFile,
  onMoveFile,
  onCreateSubdir,
  onDeleteDir,
  depth = 0,
}: DirectoryTreeViewProps) {
  const expandedDirsArray = useSettingsStore((state) => state.expandedDirs);
  const toggleExpandedDir = useSettingsStore(
    (state) => state.toggleExpandedDir,
  );
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
          onViewChunks={() => onViewChunks(child.path)}
          onRechunk={() => onRechunk(child.path)}
          onReconvert={() => onReconvert(child.path)}
          onRemove={() => onRemoveFile(child.path)}
          onMove={() => onMoveFile(child.path)}
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
          onCreateSubdir={() => onCreateSubdir(child.path)}
          onDeleteDir={() => onDeleteDir(child.path)}
        />
        {isExpanded &&
          (child.children ?? []).map((grandchild) =>
            renderEntry(grandchild, currentDepth + 1),
          )}
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
