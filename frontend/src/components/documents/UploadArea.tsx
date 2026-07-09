import {
  ChevronDown,
  FileText,
  FolderOpen,
  Inbox,
  Plus,
  type LucideIcon,
  Upload,
  X,
} from "lucide-react";

import { PERSONAL_SCOPE, formatTarget } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface UploadAreaProps {
  isDragging: boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  directoryInputRef: React.RefObject<HTMLInputElement | null>;
  /** Canonical directory uploads and new documents land in. */
  target: string;
  /** Reset the target back to the personal workspace root. */
  onResetTarget: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onFileInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDirectoryInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onSelectFiles: () => void;
  onSelectDirectory: () => void;
  onNewDocument: () => void;
  onNewFolder: () => void;
}

/**
 * A single toolbar button that fans out to a file- and a folder-scoped action,
 * keeping the Upload and Create menus identical in styling and behaviour.
 */
function ActionMenu({
  label,
  icon: Icon,
  onFile,
  onFolder,
}: {
  label: string;
  icon: LucideIcon;
  onFile: () => void;
  onFolder: () => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" size="sm">
          <Icon className="h-4 w-4 mr-1" />
          {label}
          <ChevronDown className="h-3.5 w-3.5 ml-1 opacity-70" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuItem onClick={onFile}>
          <FileText className="h-4 w-4 mr-2" />
          File
        </DropdownMenuItem>
        <DropdownMenuItem onClick={onFolder}>
          <FolderOpen className="h-4 w-4 mr-2" />
          Folder
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// The drop zone stays interactive while uploads are in flight: dropping more
// files appends them to the upload queue (surfaced in the background-task tray)
// rather than replacing or cancelling the work already running.
export function UploadArea({
  isDragging,
  fileInputRef,
  directoryInputRef,
  target,
  onResetTarget,
  onDragOver,
  onDragLeave,
  onDrop,
  onFileInputChange,
  onDirectoryInputChange,
  onSelectFiles,
  onSelectDirectory,
  onNewDocument,
  onNewFolder,
}: UploadAreaProps) {
  const atPersonalRoot = target === PERSONAL_SCOPE;

  return (
    <div className="border-b p-4">
      <div
        className={`flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-4 transition-colors ${
          isDragging
            ? "border-primary bg-primary/10"
            : "border-muted-foreground/25 bg-muted/25 hover:border-muted-foreground/50 hover:bg-muted/50"
        }`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          aria-label="Upload files"
          onChange={onFileInputChange}
        />
        <input
          ref={directoryInputRef}
          type="file"
          // @ts-expect-error webkitdirectory is not in React's type definitions
          webkitdirectory=""
          multiple
          className="hidden"
          aria-label="Upload directory"
          onChange={onDirectoryInputChange}
        />
        {/* A drop-zone icon (dashed frame + pointer) marks the dashed area as a
            drop target — deliberately not the Upload icon used by the menu below. */}
        <div className="flex max-w-full items-center gap-2">
          <Inbox className="h-8 w-8 shrink-0 text-muted-foreground" />
          <span className="text-sm text-muted-foreground">Drop files here to upload</span>
        </div>
        <div className="flex flex-wrap justify-center gap-2">
          {/* Upload > File accepts any files, including ZIP archives, which are
              extracted into a collection rather than stored as one document. */}
          <ActionMenu
            label="Upload"
            icon={Upload}
            onFile={onSelectFiles}
            onFolder={onSelectDirectory}
          />
          <ActionMenu label="Create" icon={Plus} onFile={onNewDocument} onFolder={onNewFolder} />
        </div>
        {/* Where drops, uploads, and new files land — click a folder in the tree
            to change it. */}
        <div
          className="flex max-w-full items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-sm"
          title="Uploads, new files and folders land here. Click a folder in the tree to change it."
        >
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="shrink-0 text-muted-foreground">Working in</span>
          <span className="min-w-0 truncate font-medium">{formatTarget(target)}</span>
          {!atPersonalRoot && (
            <Button
              variant="ghost"
              size="icon"
              className="h-4 w-4 shrink-0"
              title="Reset to personal root"
              onClick={onResetTarget}
            >
              <X className="h-3 w-3" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
