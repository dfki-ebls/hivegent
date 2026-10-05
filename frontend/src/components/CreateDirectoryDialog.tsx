import { useTranslation } from "react-i18next";

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
  const { t } = useTranslation();

  return (
    <NameInputDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t(($) => $.documents.confirm.createFolder)}
      description={t(($) => $.documents.confirm.createFolderDescription, {
        target: formatTarget(target),
      })}
      label={t(($) => $.documents.confirm.folderName)}
      placeholder={t(($) => $.documents.confirm.folderPlaceholder)}
      submitLabel={t(($) => $.common.actions.create)}
      onSubmit={onCreate}
    />
  );
}
