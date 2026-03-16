import { useState } from "react";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Input } from "./ui/input";

interface MoveDocumentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Single-file or directory mode: current path. */
  currentPath?: string;
  /** Set to true when moving a directory instead of a file. */
  isDirectory?: boolean;
  /** Bulk mode: number of files being moved. */
  bulkFileCount?: number;
  /** Called with the new full path (single/directory) or destination directory (bulk). */
  onMove: (destination: string) => void;
}

export function MoveDocumentDialog({
  open,
  onOpenChange,
  currentPath,
  isDirectory,
  bulkFileCount,
  onMove,
}: MoveDocumentDialogProps) {
  const isBulk = bulkFileCount != null && bulkFileCount > 0;
  const [destination, setDestination] = useState(currentPath ?? "");

  const handleOpen = (isOpen: boolean) => {
    if (isOpen) {
      setDestination(isBulk ? "" : (currentPath ?? ""));
    }
    onOpenChange(isOpen);
  };

  const canSubmit = isBulk
    ? destination.trim().length > 0
    : destination.trim().length > 0 && destination.trim() !== currentPath;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (canSubmit) {
      onMove(destination.trim());
      onOpenChange(false);
    }
  };

  let title: string;
  let description: string;
  let inputLabel: string;
  let placeholder: string;
  if (isBulk) {
    title = `Move ${bulkFileCount} Documents`;
    description = "Enter the destination directory for the selected documents.";
    inputLabel = "Destination directory";
    placeholder = "projects/reports";
  } else if (isDirectory) {
    title = "Move Directory";
    description = "Enter the new path for the directory.";
    inputLabel = "New path";
    placeholder = "projects/reports";
  } else {
    title = "Move Document";
    description = "Enter the new path for the document. Include the filename.";
    inputLabel = "New path";
    placeholder = "projects/report.md";
  }

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          {!isBulk && currentPath && (
            <div className="space-y-2">
              <span className="text-sm text-muted-foreground">Current path</span>
              <p className="text-sm font-mono bg-muted rounded px-2 py-1">{currentPath}</p>
            </div>
          )}
          <div className="space-y-2">
            <label htmlFor="move-destination" className="text-sm font-medium">
              {inputLabel}
            </label>
            <Input
              id="move-destination"
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              placeholder={placeholder}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              Move
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
