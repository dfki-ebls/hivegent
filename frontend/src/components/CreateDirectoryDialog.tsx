import { useState } from "react";
import { formatTarget } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

interface CreateDirectoryDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Canonical directory the new folder is created in. */
  target: string;
  /** Called with the entered folder name (relative to `target`). */
  onCreate: (name: string) => void;
}

export function CreateDirectoryDialog({
  open,
  onOpenChange,
  target,
  onCreate,
}: CreateDirectoryDialogProps) {
  const [dirName, setDirName] = useState("");

  const handleOpen = (isOpen: boolean) => {
    if (isOpen) {
      setDirName("");
    }
    onOpenChange(isOpen);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = dirName.trim();
    if (trimmed) {
      onCreate(trimmed);
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create Folder</DialogTitle>
          <DialogDescription>Create a new folder in {formatTarget(target)}.</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="directory-name" className="text-sm font-medium">
              Folder name
            </label>
            <Input
              id="directory-name"
              value={dirName}
              onChange={(e) => setDirName(e.target.value)}
              placeholder="new-folder"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!dirName.trim()}>
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
