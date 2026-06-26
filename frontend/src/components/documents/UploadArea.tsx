import { Archive, FolderOpen, FolderPlus, Paperclip, Plus, Upload } from "lucide-react";

import { Button } from "../ui/button";

interface UploadAreaProps {
  isDragging: boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  directoryInputRef: React.RefObject<HTMLInputElement | null>;
  zipInputRef: React.RefObject<HTMLInputElement | null>;
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

// The drop zone stays interactive while uploads are in flight: dropping more
// files appends them to the upload queue (surfaced in the background-task tray)
// rather than replacing or cancelling the work already running.
export function UploadArea({
  isDragging,
  fileInputRef,
  directoryInputRef,
  zipInputRef,
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
