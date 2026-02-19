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
  currentPath: string;
  onMove: (destination: string) => void;
}

export function MoveDocumentDialog({
  open,
  onOpenChange,
  currentPath,
  onMove,
}: MoveDocumentDialogProps) {
  const [destination, setDestination] = useState(currentPath);

  const handleOpen = (isOpen: boolean) => {
    if (isOpen) {
      setDestination(currentPath);
    }
    onOpenChange(isOpen);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = destination.trim();
    if (trimmed && trimmed !== currentPath) {
      onMove(trimmed);
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Move Document</DialogTitle>
          <DialogDescription>
            Enter the new path for the document. Include the filename.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm text-muted-foreground">
              Current path
            </label>
            <p className="text-sm font-mono bg-muted rounded px-2 py-1">
              {currentPath}
            </p>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">New path</label>
            <Input
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              placeholder="projects/report.md"
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={
                !destination.trim() || destination.trim() === currentPath
              }
            >
              Move
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
