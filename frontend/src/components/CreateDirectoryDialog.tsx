import { useState } from "react";
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
  parentPath?: string;
  onCreate: (path: string) => void;
}

export function CreateDirectoryDialog({
  open,
  onOpenChange,
  parentPath,
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
      const fullPath = parentPath ? `${parentPath}/${trimmed}` : trimmed;
      onCreate(fullPath);
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create Directory</DialogTitle>
          <DialogDescription>
            {parentPath
              ? `Create a new directory inside "${parentPath}".`
              : "Create a new directory in the documents root."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="directory-name" className="text-sm font-medium">
              Directory name
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
