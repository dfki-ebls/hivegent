import { formatTarget } from "@/lib/api";
import { NameInputDialog } from "@/components/documents/NameInputDialog";

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
  return (
    <NameInputDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Create Folder"
      description={`Create a new folder in ${formatTarget(target)}.`}
      label="Folder name"
      placeholder="new-folder"
      submitLabel="Create"
      onSubmit={onCreate}
    />
  );
}
