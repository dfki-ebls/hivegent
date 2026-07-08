import { Archive, FolderOpen, FolderPlus, Paperclip, Plus, Upload, X } from "lucide-react";

import { PERSONAL_SCOPE, splitScopePath } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface UploadAreaProps {
  isDragging: boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  directoryInputRef: React.RefObject<HTMLInputElement | null>;
  zipInputRef: React.RefObject<HTMLInputElement | null>;
  /** Canonical directory uploads and new documents land in. */
  target: string;
  /** Reset the target back to the personal workspace root. */
  onResetTarget: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onFileInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDirectoryInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onZipInputChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onSelectFiles: () => void;
  onSelectDirectory: () => void;
  onSelectZip: () => void;
  onNewDocument: () => void;
  onNewFolder: () => void;
}

/** Human-readable breadcrumb for a canonical target directory. */
function formatTarget(target: string): string {
  const { scope, local } = splitScopePath(target);
  const scopeLabel = scope === PERSONAL_SCOPE ? "Personal" : scope.slice(1);
  return local ? `${scopeLabel} / ${local.replaceAll("/", " / ")}` : scopeLabel;
}

// The drop zone stays interactive while uploads are in flight: dropping more
// files appends them to the upload queue (surfaced in the background-task tray)
// rather than replacing or cancelling the work already running.
export function UploadArea({
  isDragging,
  fileInputRef,
  directoryInputRef,
  zipInputRef,
  target,
  onResetTarget,
  onDragOver,
  onDragLeave,
  onDrop,
  onFileInputChange,
  onDirectoryInputChange,
  onZipInputChange,
  onSelectFiles,
  onSelectDirectory,
  onSelectZip,
  onNewDocument,
  onNewFolder,
}: UploadAreaProps) {
  const atPersonalRoot = target === PERSONAL_SCOPE;

  return (
    <div className="border-b p-4">
      <div
        className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-6 transition-colors ${
          isDragging
            ? "border-primary bg-primary/10"
            : "border-muted-foreground/25 bg-muted/25 hover:border-muted-foreground/50 hover:bg-muted/50"
        }`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <Upload className="h-10 w-10 text-muted-foreground" />
        <div className="text-center">
          <p className="font-medium">Drop files here to upload</p>
          <p className="text-sm text-muted-foreground">or click to browse</p>
        </div>
        {/* Where drops, uploads, and new documents land — click a folder in the
            tree to change it. Kept inside the drop zone so it costs no extra row. */}
        <div
          className="flex max-w-full items-center gap-1.5 rounded-md border bg-background px-2 py-1 text-sm"
          title="Uploads, new documents and folders land here. Click a folder in the tree to change it."
        >
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="shrink-0 text-muted-foreground">Uploading to</span>
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
        <input
          ref={zipInputRef}
          type="file"
          accept=".zip"
          className="hidden"
          aria-label="Upload zip archive"
          onChange={onZipInputChange}
        />
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={onSelectFiles}>
            <Paperclip className="h-4 w-4 mr-1" />
            Select Files
          </Button>
          <Button variant="secondary" size="sm" onClick={onSelectDirectory}>
            <FolderOpen className="h-4 w-4 mr-1" />
            Upload Folder
          </Button>
          <Button variant="secondary" size="sm" onClick={onSelectZip}>
            <Archive className="h-4 w-4 mr-1" />
            Upload ZIP
          </Button>
        </div>
        <div className="flex flex-col items-center gap-2 pt-4 border-t border-muted-foreground/15 w-full">
          <p className="text-xs text-muted-foreground">
            Or create and edit documents directly in the browser
          </p>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onNewDocument}>
              <Plus className="h-4 w-4 mr-1" />
              New Document
            </Button>
            <Button variant="outline" size="sm" onClick={onNewFolder}>
              <FolderPlus className="h-4 w-4 mr-1" />
              New Folder
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
